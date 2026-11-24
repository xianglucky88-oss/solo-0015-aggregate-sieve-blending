"""Exercise whole-package custody for offline cabinets: out-of-order vouchers, both restore policies."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from workbench_check import QuietEndpoint, ROOT
from workbench import profile
from workbench.db import initialize
from workbench.service import Service

DOC = json.loads((ROOT / 'examples/workbench.json').read_text(encoding='utf-8'))
DOC['archived_assays'] = {
    'p': {'sieves_um': [4000, 3000, 2000], 'dry_g': 100, 'retained_g': [0, 40, 60], 'pan_g': 0},
    'q': {'sieves_um': [4000, 2000], 'dry_g': 60, 'retained_g': [20, 30], 'pan_g': 10},
}
DOC['archive_handoff'] = {'holds': [],
                          'orders': {'onsite-30': {'specimen': 'p', 'bounds_um': [4000, 2000], 'mass_g': 30}}}
TEXT = json.dumps(DOC)
NAME = 'custody-archive'
CUSTODY = '/api/archive-custody/'
STOCK = '/api/archive-stock/'
P_CELL = {'specimen': 'p', 'bounds_um': [4000, 2000]}
Q_CELL = {'specimen': 'q', 'bounds_um': [4000, 2000]}


def fixture(path):
    initialize(path, profile.PRODUCT)
    service = Service(path, profile)
    draft = service.post('/api/stage', {'dataset': NAME, 'source': 'custody.json', 'text': TEXT})
    assert draft['state'] == 'ready'
    service.post('/api/publish', {'draft': draft['id']})
    run = service.post('/api/run', {'dataset': NAME, 'operations': ['archive-handoff']})['runs'][0]
    assert run['output']['state'] == 'ready'
    return run


def exercise(call, run, policy):
    def p_cell():
        report = call(STOCK + 'reconcile', {'run': run['id']})
        row, = [c for c in report['cells'] if c['specimen'] == 'p' and c['bounds_um'] == [4000, 2000]]
        return report, row

    def custody_report():
        return call(CUSTODY + 'reconcile', {'dataset': NAME})

    def package(seal):
        for entry in custody_report()['rounds']:
            for found in entry['packages']:
                if found['seal'] == seal:
                    return found
        raise AssertionError(seal)

    start = {'run': run['id'], 'generation': 1}
    call(CUSTODY + 'reconcile', {'dataset': NAME}, fault=(409, 'stock_not_open'))
    call(CUSTODY + 'dispatch', {'dataset': NAME, 'ticket': 'early', 'cabinet': 'cabinet-a',
                                'packages': [{'seal': 'seal-early', **P_CELL, 'mass_g': 1}],
                                'orders': {'x': {**P_CELL, 'mass_g': 1, 'kind': 'guaranteed'}}},
         fault=(409, 'stock_not_open'))
    call(STOCK + 'open', start)
    _, cell = p_cell()
    assert (cell['on_hand_g'], cell['custody_held_g'], cell['available_g']) == (100, 0, 100)

    # 同档 40、30、30 克三包：甲柜接 40 和 30，乙柜接另一包 30；每包唯一封签、实称干重、原筛档。
    dispatch_a = {'dataset': NAME, 'ticket': 'dispatch-a', 'cabinet': 'cabinet-a',
                  'packages': [{'seal': 'seal-a-40', **P_CELL, 'mass_g': 40},
                               {'seal': 'seal-a-30', **P_CELL, 'mass_g': 30}],
                  'orders': {'a-base-40': {**P_CELL, 'mass_g': 40, 'kind': 'guaranteed'},
                             'a-standby-30': {**P_CELL, 'mass_g': 30, 'kind': 'standby'}}}
    round_a = call(CUSTODY + 'dispatch', dispatch_a)
    assert [(p['seal'], p['mass_g']) for p in round_a['packages']] == [('seal-a-30', 30), ('seal-a-40', 40)]
    assert all(p['specimen'] == 'p' and p['bounds_um'] == [4000, 2000] for p in round_a['packages'])
    # 出柜前按实际包组合核对：甲保底 40 可保，30 克候补也有余包可接。
    assert {o['order']: o['acceptable'] for o in round_a['orders']} == {'a-base-40': 1, 'a-standby-30': 1}
    assert call(CUSTODY + 'dispatch', dispatch_a) == round_a
    call(CUSTODY + 'dispatch', {**dispatch_a, 'cabinet': 'cabinet-z'}, fault=(409, 'custody_ticket_conflict'))

    dispatch_b = {'dataset': NAME, 'ticket': 'dispatch-b', 'cabinet': 'cabinet-b',
                  'packages': [{'seal': 'seal-b-30', **P_CELL, 'mass_g': 30}],
                  'orders': {'b-base-30': {**P_CELL, 'mass_g': 30, 'kind': 'guaranteed'},
                             'b-standby-30': {**P_CELL, 'mass_g': 30, 'kind': 'standby'}}}
    round_b = call(CUSTODY + 'dispatch', dispatch_b)
    # 只有甲具备离线接单条件：乙的 30 克候补没有剩余整包，不可接。
    assert {o['order']: o['acceptable'] for o in round_b['orders']} == {'b-base-30': 1, 'b-standby-30': 0}

    def bad_dispatch(ticket, packages, orders, fault):
        call(CUSTODY + 'dispatch', {'dataset': NAME, 'ticket': ticket, 'cabinet': 'cabinet-c',
                                    'packages': packages, 'orders': orders}, fault=fault)

    bad_dispatch('bad-cover', [{'seal': 'seal-x-30', **Q_CELL, 'mass_g': 30}],
                 {'x-g-50': {**Q_CELL, 'mass_g': 50, 'kind': 'guaranteed'}}, (409, 'custody_guaranteed_uncoverable'))
    bad_dispatch('bad-stock', [{'seal': 'seal-y-40', **Q_CELL, 'mass_g': 40}],
                 {'x-g-40': {**Q_CELL, 'mass_g': 40, 'kind': 'guaranteed'}}, (409, 'custody_stock_short'))
    bad_dispatch('bad-cell', [{'seal': 'seal-z-10', 'specimen': 'p', 'bounds_um': [3000, 1000], 'mass_g': 10}],
                 {'x-g-10': {'specimen': 'p', 'bounds_um': [3000, 1000], 'mass_g': 10, 'kind': 'guaranteed'}},
                 (409, 'custody_cell_unknown'))
    bad_dispatch('bad-seal', [{'seal': 'seal-a-40', **Q_CELL, 'mass_g': 5}],
                 {'x-g-5': {**Q_CELL, 'mass_g': 5, 'kind': 'guaranteed'}}, (409, 'custody_seal_reused'))
    bad_dispatch('bad-mass', [{'seal': 'seal-m-1', **Q_CELL, 'mass_g': 0}],
                 {'x-g-1': {**Q_CELL, 'mass_g': 1, 'kind': 'guaranteed'}}, (400, 'custody_mass'))

    dispatch_c = {'dataset': NAME, 'ticket': 'dispatch-c', 'cabinet': 'cabinet-c',
                  'packages': [{'seal': 'seal-c-30', **Q_CELL, 'mass_g': 30}],
                  'orders': {'c-base-30': {**Q_CELL, 'mass_g': 30, 'kind': 'guaranteed'},
                             'c-standby-p': {**P_CELL, 'mass_g': 30, 'kind': 'standby'}}}
    round_c = call(CUSTODY + 'dispatch', dispatch_c)
    assert {o['order']: o['acceptable'] for o in round_c['orders']} == {'c-base-30': 1, 'c-standby-p': 0}

    # 托管中的包从总库可领量扣开，但没有实发票就不计作已发。
    _, cell = p_cell()
    assert (cell['on_hand_g'], cell['custody_held_g'], cell['custody_issued_g'], cell['available_g']) == (100, 100, 0, 0)
    call(STOCK + 'issue', {**start, 'order': 'onsite-30', 'ticket': 'onsite-early', 'mass_g': 30},
         fault=(409, 'stock_hold_or_balance'))

    # 柜台只能动用本柜接收的整包：不得跨柜、跨筛档或拆包凑数。
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'v-1', 'round': round_a['round'],
                             'seal': 'seal-b-30', 'order': 'a-base-40'}, fault=(409, 'custody_package_foreign'))
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'v-2', 'round': round_a['round'],
                             'seal': 'seal-a-40', 'order': 'no-such-order'}, fault=(409, 'custody_order_unknown'))
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'v-3', 'round': round_a['round'],
                             'seal': 'seal-a-40', 'order': 'a-standby-30'}, fault=(409, 'custody_order_quota'))
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'v-4', 'round': round_b['round'],
                             'seal': 'seal-b-30', 'order': 'b-standby-30'}, fault=(409, 'custody_order_not_accepted'))
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'v-5', 'round': round_c['round'],
                             'seal': 'seal-c-30', 'order': 'c-standby-p'}, fault=(409, 'custody_cell_mismatch'))
    call(CUSTODY + 'verify', {'dataset': NAME, 'ticket': 'v-6', 'seal': 'seal-ghost'},
         fault=(404, 'custody_package_not_found'))
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'v-7', 'round': '0' * 64,
                             'seal': 'seal-a-40', 'order': 'a-base-40'}, fault=(404, 'custody_round_not_found'))
    ok_c = call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-c-30', 'round': round_c['round'],
                                    'seal': 'seal-c-30', 'order': 'c-base-30'})
    assert ok_c['kind'] == 'issue' and ok_c['mass_g'] == 30

    # 甲柜离线：实际发了 40 克、没接候补，未发的 30 克原封带回；交回凭据先到，40 克实发票后到。
    if policy == 'package':
        # 交回凭据先到、实物尚未验签：口头退柜不能供现场单。
        ret = call(CUSTODY + 'return', {'dataset': NAME, 'ticket': 'ret-a-30',
                                        'round': round_a['round'], 'seal': 'seal-a-30'})
        assert package('seal-a-30')['state'] == 'pending'
        assert package('seal-a-30')['voucher_received'] and not package('seal-a-30')['seal_verified']
        call(STOCK + 'issue', {**start, 'order': 'onsite-30', 'ticket': 'onsite-1', 'mass_g': 30},
             fault=(409, 'stock_hold_or_balance'))
        # 总库现场验到封签：该包归属独立确认，包级策略当即恢复可领，现场单此刻拿到料。
        ver = call(CUSTODY + 'verify', {'dataset': NAME, 'ticket': 'scan-a-30', 'seal': 'seal-a-30'})
        assert package('seal-a-30')['state'] == 'restored'
        _, cell = p_cell()
        assert (cell['custody_held_g'], cell['available_g']) == (70, 30)
        call(STOCK + 'issue', {**start, 'order': 'onsite-30', 'ticket': 'onsite-1', 'mass_g': 30})
        tkt = call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-a-40', 'round': round_a['round'],
                                       'seal': 'seal-a-40', 'order': 'a-base-40'})
    else:
        # 先扫到实物但交回凭据未到：归属尚不能确认，记待对账，不默认为可领。
        ver = call(CUSTODY + 'verify', {'dataset': NAME, 'ticket': 'scan-a-30', 'seal': 'seal-a-30'})
        assert package('seal-a-30')['state'] == 'pending'
        assert package('seal-a-30')['seal_verified'] and not package('seal-a-30')['voucher_received']
        call(STOCK + 'issue', {**start, 'order': 'onsite-30', 'ticket': 'onsite-1', 'mass_g': 30},
             fault=(409, 'stock_hold_or_balance'))
        # 交回凭据到：归属确认，但轮级策略要等本轮全部对平才恢复，现场单仍领不到。
        ret = call(CUSTODY + 'return', {'dataset': NAME, 'ticket': 'ret-a-30',
                                        'round': round_a['round'], 'seal': 'seal-a-30'})
        assert package('seal-a-30')['state'] == 'verified'
        _, cell = p_cell()
        assert cell['available_g'] == 0
        call(STOCK + 'issue', {**start, 'order': 'onsite-30', 'ticket': 'onsite-1', 'mass_g': 30},
             fault=(409, 'stock_hold_or_balance'))
        # 40 克实发票到：本轮对平，交回包随之恢复，现场单此刻才能拿到料。
        tkt = call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-a-40', 'round': round_a['round'],
                                       'seal': 'seal-a-40', 'order': 'a-base-40'})
        assert package('seal-a-30')['state'] == 'restored'
        _, cell = p_cell()
        assert cell['available_g'] == 30
        call(STOCK + 'issue', {**start, 'order': 'onsite-30', 'ticket': 'onsite-1', 'mass_g': 30})

    # 后到实发票入账后，两种策略给出相同的实际已发和结余。
    report, cell = p_cell()
    assert (cell['opening_g'], cell['on_hand_g'], cell['custody_held_g'],
            cell['custody_issued_g'], cell['available_g']) == (100, 30, 30, 40, 0)
    onsite = next(o for o in report['orders'] if o['order'] == 'onsite-30')
    assert (onsite['net_used_g'], onsite['difference_g']) == (30, 0)

    # 凭据幂等：重复交回凭据、重复扫码、实发票重放都返回首次记录，不重复恢复。
    assert call(CUSTODY + 'return', {'dataset': NAME, 'ticket': 'ret-a-30',
                                     'round': round_a['round'], 'seal': 'seal-a-30'}) == ret
    call(CUSTODY + 'return', {'dataset': NAME, 'ticket': 'ret-a-30-dup',
                              'round': round_a['round'], 'seal': 'seal-a-30'}, fault=(409, 'custody_return_duplicate'))
    assert call(CUSTODY + 'verify', {'dataset': NAME, 'ticket': 'scan-a-30', 'seal': 'seal-a-30'}) == ver
    call(CUSTODY + 'verify', {'dataset': NAME, 'ticket': 'scan-a-30-dup', 'seal': 'seal-a-30'},
         fault=(409, 'custody_scan_duplicate'))
    assert call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-a-40', 'round': round_a['round'],
                                    'seal': 'seal-a-40', 'order': 'a-base-40'}) == tkt
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-a-40', 'round': round_a['round'],
                             'seal': 'seal-a-40', 'order': 'a-standby-30'}, fault=(409, 'custody_ticket_conflict'))
    # 一包一旦记作实发或交回，就不能再供同一轮柜台领用。
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-a-40-again', 'round': round_a['round'],
                             'seal': 'seal-a-40', 'order': 'a-base-40'}, fault=(409, 'custody_package_spent'))
    call(CUSTODY + 'return', {'dataset': NAME, 'ticket': 'ret-a-40', 'round': round_a['round'],
                              'seal': 'seal-a-40'}, fault=(409, 'custody_package_spent'))
    call(CUSTODY + 'issue', {'dataset': NAME, 'ticket': 'issue-a-30-late', 'round': round_a['round'],
                             'seal': 'seal-a-30', 'order': 'a-standby-30'}, fault=(409, 'custody_package_spent'))
    _, cell = p_cell()
    assert (cell['custody_held_g'], cell['available_g'], cell['on_hand_g']) == (30, 0, 30)

    # 反复补账不能让未回包重入总库；托管实发票不是台账出库票，不能走补账。
    call(STOCK + 'correct', {'dataset': NAME, 'ticket': 'amend-1', 'issue_ticket': 'onsite-1',
                             'basis': 'onsite-1', 'issued_g': 25, 'reason': 'Reweighed the signed slip'})
    _, cell = p_cell()
    assert (cell['on_hand_g'], cell['available_g'], cell['custody_held_g']) == (35, 5, 30)
    call(STOCK + 'correct', {'dataset': NAME, 'ticket': 'amend-2', 'issue_ticket': 'onsite-1',
                             'basis': 'amend-1', 'issued_g': 61, 'reason': 'Beyond physical balance'},
         fault=(409, 'stock_physical_balance'))
    call(STOCK + 'correct', {'dataset': NAME, 'ticket': 'amend-2', 'issue_ticket': 'onsite-1',
                             'basis': 'amend-1', 'issued_g': 30, 'reason': 'Back to the signed amount'})
    call(STOCK + 'correct', {'dataset': NAME, 'ticket': 'amend-custody', 'issue_ticket': 'issue-a-40',
                             'basis': 'issue-a-40', 'issued_g': 35, 'reason': 'Not a ledger ticket'},
         fault=(404, 'stock_issue_not_found'))
    _, cell = p_cell()
    assert (cell['on_hand_g'], cell['custody_held_g'], cell['available_g']) == (30, 30, 0)

    # 恢复旧计划也不能让未回包重入总库；不交柜的领样流程照常受代次守卫。
    before = custody_report()
    revised = json.loads(TEXT)
    revised['audit_note'] = 'second delivery'
    draft = call('/api/stage', {'dataset': NAME, 'source': 'revised.json', 'text': json.dumps(revised)})
    call('/api/publish', {'draft': draft['id']})
    call('/api/restore', {'dataset': NAME, 'revision': run['revision'], 'generation': 2})
    assert custody_report() == before
    _, cell = p_cell()
    assert (cell['on_hand_g'], cell['custody_held_g'], cell['available_g']) == (30, 30, 0)
    call(STOCK + 'issue', {'run': run['id'], 'generation': 1, 'order': 'onsite-30',
                           'ticket': 'onsite-stale', 'mass_g': 1}, fault=(409, 'stock_activation_changed'))

    final = custody_report()
    rounds = {entry['cabinet']: entry for entry in final['rounds']}
    assert rounds['cabinet-a']['state'] == 'closed'
    assert rounds['cabinet-c']['state'] == 'closed'
    assert rounds['cabinet-b']['state'] == 'open'
    assert package('seal-b-30')['state'] == 'held'
    orders_a = {o['order']: o for o in rounds['cabinet-a']['orders']}
    assert orders_a['a-base-40']['issued_g'] == 40
    assert orders_a['a-standby-30']['issued_g'] == 0
    kinds = [e['kind'] for e in rounds['cabinet-a']['events']]
    assert kinds == (['dispatch', 'return', 'verify', 'issue'] if policy == 'package'
                     else ['dispatch', 'verify', 'return', 'issue'])
    _, cell = p_cell()
    return {'issued_g': cell['opening_g'] - cell['on_hand_g'], 'on_hand_g': cell['on_hand_g'],
            'available_g': cell['available_g'], 'custody_held_g': cell['custody_held_g']}


def scenario(policy):
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / 'custody.sqlite3'
        run = fixture(db)
        shim = SimpleNamespace(**vars(profile))
        shim.CUSTODY_RESTORE = policy
        with ThreadingHTTPServer(('127.0.0.1', 0), QuietEndpoint) as server:
            server.service = Service(db, shim)
            worker = Thread(target=server.serve_forever)
            worker.start()

            def call(route, data=None, *, fault=None):
                url = f'http://127.0.0.1:{server.server_port}{route}'
                request = Request(url) if data is None else Request(
                    url, json.dumps(data).encode(), {'Content-Type': 'application/json'})
                try:
                    with urlopen(request, timeout=10) as response:
                        assert fault is None, (route, fault)
                        return json.load(response)
                except HTTPError as exc:
                    with exc:
                        got = exc.code, json.load(exc)['error']
                    assert fault == got, (route, got, fault)

            try:
                return exercise(call, run, policy)
            finally:
                server.shutdown()
                worker.join()


def main():
    package_level = scenario('package')
    round_level = scenario('round')
    assert package_level == round_level == {'issued_g': 70, 'on_hand_g': 30,
                                            'available_g': 0, 'custody_held_g': 30}
    print('Archive custody: dispatch checks, out-of-order vouchers, both restore policies and prohibitions passed')


if __name__ == '__main__':
    main()
