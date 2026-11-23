"""End-to-end acceptance for the granular-mix commitment bench over HTTP.

Reference opening position (all synthetic):

* rework bucket R-80: 120 g original mix, 60 g through the target sieve, the
  previous order consumed 40 g with only 12-28 g certified through the sieve;
  the 80 g returned mass was confirmed re-mixed, so its certified passing mass
  is 32-48 g, i.e. a 40%-60% passing fraction;
* fresh bucket F-60: 60 g, all certified through the sieve (100%).

Leftovers are booked as a *new* bucket, never returned to the original sieve
shelf as fresh material.
"""
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workbench import profile
from workbench.db import connect, initialize, transaction, _apply_schema
from workbench.http import Endpoint
from workbench.service import Service

DATASET = 'mix-bench'
SIEVE = 4750
MIX = '/api/mix/'


class QuietEndpoint(Endpoint):
    def log_message(self, *args):
        pass


def rework_admission(ticket='admit-rework'):
    return {
        'dataset': DATASET, 'ticket': ticket,
        'bucket': {'code': 'R-80', 'mix_batch': 'MB-1'},
        'original_mix_g': 120, 'used_g': 40, 'returned_g': 80,
        'origin_evidence': [{'sieve_um': SIEVE, 'original_passing_g': 60,
                             'used_passing_g': [12, 28]}],
    }


def fresh_admission(ticket='admit-fresh'):
    return {
        'dataset': DATASET, 'ticket': ticket,
        'bucket': {'code': 'F-60', 'net_g': 60},
        'passing_evidence_g': [{'sieve_um': SIEVE, 'min_g': 60, 'max_g': 60}],
    }


def order(order_id, grams, lower, upper, *, rework_only=False, allowed=None):
    doc = {'dataset': DATASET, 'order': order_id, 'dry_g': grams,
           'sieves_um': [SIEVE], 'limits_bp': [[lower, upper]], 'rework_only': rework_only}
    if allowed is not None:
        doc['allowed_buckets'] = allowed
    return doc


def open_bench(call):
    rework = call(MIX + 'admit-rework', rework_admission())
    assert rework['kind'] == 'admit' and rework['bucket'] == 'R-80' and rework['mass_g'] == 80
    fresh = call(MIX + 'admit-fresh', fresh_admission())
    assert fresh['bucket'] == 'F-60' and fresh['mass_g'] == 60
    opening = call(MIX + 'status', {'dataset': DATASET})
    by_code = {row['bucket']: row for row in opening['buckets']}
    assert by_code['R-80']['kind'] == 'rework' and by_code['R-80']['evidence'] == [[SIEVE, 32, 48]]
    assert by_code['F-60']['kind'] == 'fresh' and by_code['F-60']['evidence'] == [[SIEVE, 60, 60]]
    return opening


def admission_guards(call):
    assert call(MIX + 'admit-rework', rework_admission())
    call(MIX + 'admit-rework', {**rework_admission(), 'ticket': 'dup-bucket'},
         fault=(409, 'mix_bucket_exists'))
    bad = rework_admission('bad-evidence')
    bad['origin_evidence'][0]['used_passing_g'] = [45, 48]
    call(MIX + 'admit-rework', bad, fault=(422, 'mix_evidence_inconsistent'))
    unbalanced = rework_admission('unbalanced')
    unbalanced['returned_g'] = 79
    call(MIX + 'admit-rework', unbalanced, fault=(422, 'mix_mass_balance'))
    no_batch = rework_admission('no-batch')
    del no_batch['bucket']['mix_batch']
    call(MIX + 'admit-rework', no_batch, fault=(422, 'mix_batch_required'))
    call(MIX + 'admit-fresh', fresh_admission())


def probe_rework_only(call):
    """A rework-only >=55% order cannot occupy material on one crossing explanation."""
    open_bench(call)
    probe = call(MIX + 'explain', order('probe-55', 60, 5500, 10000, rework_only=True))
    assert probe['decision'] == 'reject_recipe_infeasible'
    assert probe['bucket_assays']['R-80']['state'] == 'possible_violation'
    assert 'F-60' not in probe['bucket_assays']
    rejected = call(MIX + 'reject', {**order('o3', 60, 5500, 10000, rework_only=True),
                                     'ticket': 'reject-o3'})
    assert rejected['kind'] == 'reject' and rejected['order'] == 'o3'
    assert call(MIX + 'reject', {**order('o3', 60, 5500, 10000, rework_only=True),
                                 'ticket': 'reject-o3'}) == rejected
    state = call(MIX + 'status', {'dataset': DATASET})
    assert all(row['reserved_g'] == 0 for row in state['buckets'])
    rejected_row = next(item for item in state['orders'] if item['order'] == 'o3')
    assert rejected_row['state'] == 'rejected' and rejected_row['reason'] == 'recipe_infeasible'
    # The same order number cannot be turned into a live commitment afterwards.
    call(MIX + 'commit-pool', {**order('o3', 60, 3500, 10000), 'ticket': 'revive-o3'},
         fault=(409, 'mix_order_exists'))


def locked_flow(call):
    """Style A: the ticket names the concrete bucket at commitment time."""
    open_bench(call)
    first = order('o1', 60, 3500, 10000, allowed=['R-80', 'F-60'])
    committed = call(MIX + 'commit-locked', {**first, 'bucket': 'R-80', 'ticket': 'lock-o1'})
    assert committed['bucket'] == 'R-80' and committed['mass_g'] == 60
    assert call(MIX + 'commit-locked', {**first, 'bucket': 'R-80', 'ticket': 'lock-o1'}) == committed
    call(MIX + 'commit-locked', {**first, 'bucket': 'R-80', 'ticket': 'other-ticket'},
         fault=(409, 'mix_order_exists'))
    call(MIX + 'commit-locked', {**first, 'bucket': 'GHOST', 'ticket': 'ghost'},
         fault=(404, 'mix_bucket_unknown'))
    state = call(MIX + 'status', {'dataset': DATASET})
    row = next(b for b in state['buckets'] if b['bucket'] == 'R-80')
    assert row['reserved_g'] == 60 and row['remaining_g'] == 20
    order_row = next(o for o in state['orders'] if o['order'] == 'o1')
    assert order_row['state'] == 'committed' and order_row['mode'] == 'locked'

    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-wrong', 'order': 'o1', 'bucket': 'F-60'},
         fault=(409, 'mix_locked_bucket_mismatch'))
    weighed = call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o1', 'order': 'o1', 'bucket': 'R-80'})
    assert weighed['bucket'] == 'R-80' and weighed['mass_g'] == 60
    assert call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o1', 'order': 'o1', 'bucket': 'R-80'}) == weighed
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-again', 'order': 'o1', 'bucket': 'R-80'},
         fault=(409, 'mix_order_already_weighed'))
    state = call(MIX + 'status', {'dataset': DATASET})
    row = next(b for b in state['buckets'] if b['bucket'] == 'R-80')
    assert row['reserved_g'] == 60 and row['remaining_g'] == 20


def pool_flow(call):
    """Style B: promise weight from the allowed bucket set without locking."""
    open_bench(call)
    first = order('o1', 60, 3500, 10000, allowed=['R-80', 'F-60'])
    # A pool promise must not accept a bucket number: candidates are not locked.
    call(MIX + 'commit-pool', {**first, 'ticket': 'sneak-lock', 'bucket': 'R-80'},
         fault=(400, 'mix_pool_has_no_locked_bucket'))
    committed = call(MIX + 'commit-pool', {**first, 'ticket': 'pool-o1'})
    assert committed['bucket'] is None and committed['mass_g'] == 60
    state = call(MIX + 'status', {'dataset': DATASET})
    order_row = next(o for o in state['orders'] if o['order'] == 'o1')
    assert order_row['mode'] == 'pool' and order_row['locked_bucket'] is None
    # Pool candidates must never be reported as a locked bucket number.
    assert committed['request']['locked_bucket'] is None
    weighed = call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o1', 'order': 'o1', 'bucket': 'R-80'})
    assert weighed['bucket'] == 'R-80'
    state = call(MIX + 'status', {'dataset': DATASET})
    order_row = next(o for o in state['orders'] if o['order'] == 'o1')
    assert order_row['state'] == 'weighed' and order_row['locked_bucket'] == 'R-80'
    remaining = {row['bucket']: row for row in state['buckets']}
    assert remaining['R-80']['remaining_g'] == 20 and remaining['F-60']['remaining_g'] == 60


def branch_a(call):
    """Second order 60 g / 35%-75%: only R-80 qualifies; F-60 violates 75%.

    A pool promise on o1 can be reshuffled onto F-60, so the pair is still
    feasible.  Once o1 is *locked* to R-80 (the preferred rework bucket),
    o2 has no certified material left and must be rejected as occupied.
    """
    open_bench(call)
    first = order('o1', 60, 3500, 10000, allowed=['R-80', 'F-60'])
    call(MIX + 'commit-pool', {**first, 'ticket': 'pool-o1'})
    second = order('o2', 60, 3500, 7500)
    explained = call(MIX + 'explain', second)
    assert explained['decision'] == 'accept_pool'
    assert explained['bucket_assays']['R-80']['state'] == 'certified'
    assert explained['bucket_assays']['F-60']['state'] == 'certain_violation'
    call(MIX + 'commit-pool', {**second, 'ticket': 'pool-o2'})
    state = call(MIX + 'status', {'dataset': DATASET})
    assert state['commitments_feasible']
    # Weighing o1 onto R-80 would consume the only bucket certified for o2.
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-strand', 'order': 'o1', 'bucket': 'R-80'},
         fault=(409, 'mix_weigh_starves_commitment'))
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o1', 'order': 'o1', 'bucket': 'F-60'})
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o2', 'order': 'o2', 'bucket': 'R-80'})
    state = call(MIX + 'status', {'dataset': DATASET})
    leftover = {row['bucket']: row for row in state['buckets']}
    assert leftover['R-80']['remaining_g'] == 20 and leftover['F-60']['remaining_g'] == 0


def branch_a_locked(call):
    """Same envelope pair, but the rework bucket is locked at ticket time."""
    open_bench(call)
    first = order('o1', 60, 3500, 10000, allowed=['R-80', 'F-60'])
    call(MIX + 'commit-locked', {**first, 'bucket': 'R-80', 'ticket': 'lock-o1'})
    second = order('o2', 60, 3500, 7500)
    explained = call(MIX + 'explain', second)
    assert explained['decision'] == 'reject_material_committed'
    assert explained['bucket_assays']['R-80']['state'] == 'certified'
    assert explained['bucket_assays']['F-60']['state'] == 'certain_violation'
    call(MIX + 'commit-pool', {**second, 'ticket': 'pool-o2'}, fault=(409, 'mix_material_committed'))
    call(MIX + 'commit-locked', {**second, 'bucket': 'R-80', 'ticket': 'lock-o2'},
         fault=(409, 'mix_material_committed'))
    call(MIX + 'reject', {**second, 'ticket': 'reject-o2'})
    rejection = call(MIX + 'status', {'dataset': DATASET})
    row = next(item for item in rejection['orders'] if item['order'] == 'o2')
    assert row['state'] == 'rejected' and row['reason'] == 'material_committed'


def branch_a_relock(call):
    """Same orders both accepted when o1 is instead promised the fresh bucket."""
    open_bench(call)
    first = order('o1', 60, 3500, 10000, allowed=['R-80', 'F-60'])
    call(MIX + 'commit-locked', {**first, 'bucket': 'F-60', 'ticket': 'lock-o1-fresh'})
    second = order('o2', 60, 3500, 7500)
    second_locked = call(MIX + 'commit-locked', {**second, 'bucket': 'R-80', 'ticket': 'lock-o2'})
    assert second_locked['bucket'] == 'R-80'
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o1', 'order': 'o1', 'bucket': 'F-60'})
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o2', 'order': 'o2', 'bucket': 'R-80'})
    state = call(MIX + 'status', {'dataset': DATASET})
    leftover = {row['bucket']: row for row in state['buckets']}
    assert leftover['R-80']['remaining_g'] == 20 and leftover['F-60']['remaining_g'] == 0
    assert state['commitments_feasible']


def branch_b(call):
    """Second order 60 g / >=90%: R-80 cannot qualify, F-60 can; both fit."""
    open_bench(call)
    first = order('o1', 60, 3500, 10000, allowed=['R-80', 'F-60'])
    call(MIX + 'commit-pool', {**first, 'ticket': 'pool-o1'})
    second = order('o2', 60, 9000, 10000)
    explained = call(MIX + 'explain', second)
    assert explained['decision'] == 'accept_pool'
    assert explained['bucket_assays']['R-80']['state'] == 'certain_violation'
    assert explained['bucket_assays']['F-60']['state'] == 'certified'
    call(MIX + 'commit-pool', {**second, 'ticket': 'pool-o2'})
    state = call(MIX + 'status', {'dataset': DATASET})
    assert state['commitments_feasible']
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-strand', 'order': 'o1', 'bucket': 'F-60'},
         fault=(409, 'mix_weigh_starves_commitment'))
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o1', 'order': 'o1', 'bucket': 'R-80'})
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o2', 'order': 'o2', 'bucket': 'F-60'})
    state = call(MIX + 'status', {'dataset': DATASET})
    leftover = {row['bucket']: row for row in state['buckets']}
    assert leftover['R-80']['remaining_g'] == 20 and leftover['F-60']['remaining_g'] == 0
    # Replaying the physical issue must not deduct the 60 g a second time.
    call(MIX + 'weigh', {'dataset': DATASET, 'ticket': 'w-o2', 'order': 'o2', 'bucket': 'F-60'})
    assert next(b for b in call(MIX + 'status', {'dataset': DATASET})['buckets']
                if b['bucket'] == 'F-60')['remaining_g'] == 0


def history_and_tickets(call):
    open_bench(call)
    call(MIX + 'commit-pool', {**order('o1', 60, 3500, 10000), 'ticket': 'pool-o1'})
    ledger = call(MIX + 'history', {'dataset': DATASET})
    kinds = [event['kind'] for event in ledger['events']]
    assert kinds == ['admit', 'admit', 'commit']
    call(MIX + 'commit-pool', {**order('other', 10, 3500, 10000), 'ticket': 'pool-o1'},
         fault=(409, 'mix_ticket_conflict'))
    page = call(MIX + 'history', {'dataset': DATASET, 'after': ledger['events'][0]['seq']})
    assert [event['ticket'] for event in page['events']] == ['admit-fresh', 'pool-o1']
    assert page['next_after'] is None


def concurrent_booking(port):
    """Two tickets racing for the only certified bucket: exactly one wins."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError

    def setup(call):
        open_bench(call)

    def call(route, data):
        request = Request(f'http://127.0.0.1:{port}{route}',
                          json.dumps(data).encode(), {'Content-Type': 'application/json'})
        with urlopen(request, timeout=10) as response:
            return response.status

    setup(lambda route, data: _plain_call(port, route, data))
    payload = order('race', 60, 9000, 10000)  # only F-60 (60 g) certifies
    gate = Barrier(2)

    def send(ticket):
        gate.wait(timeout=5)
        request = Request(f'http://127.0.0.1:{port}{MIX}commit-pool',
                          json.dumps({**payload, 'order': f'race-{ticket}', 'ticket': ticket}).encode(),
                          {'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status
        except HTTPError as exc:
            with exc:
                return exc.code

    with ThreadPoolExecutor(2) as workers:
        outcomes = sorted(workers.map(send, ['race-1', 'race-2']))
    assert outcomes == [200, 409], outcomes
    state = _plain_call(port, MIX + 'status', {'dataset': DATASET})
    fresh = next(row for row in state['buckets'] if row['bucket'] == 'F-60')
    assert fresh['reserved_g'] == 60 and fresh['remaining_g'] == 0


def _plain_call(port, route, data):
    request = Request(f'http://127.0.0.1:{port}{route}',
                      json.dumps(data).encode(), {'Content-Type': 'application/json'})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def scenario(fn):
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / 'mix.sqlite3'
        initialize(db, profile.PRODUCT)
        with ThreadingHTTPServer(('127.0.0.1', 0), QuietEndpoint) as server:
            server.service = Service(db, profile)
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                fn(make_caller(server.server_port))
            finally:
                server.shutdown()
                worker.join()


def make_caller(port):
    def call(route, data=None, *, fault=None):
        url = f'http://127.0.0.1:{port}{route}'
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
    return call


def v3_migration(tmp):
    path = Path(tmp) / 'v3.sqlite3'
    with closing(connect(path)) as db, transaction(db, write=True):
        _apply_schema(db, 'schema.sql')
        _apply_schema(db, 'handoff.sql')
        _apply_schema(db, 'archive_stock.sql')
        db.execute('INSERT INTO identity VALUES(1, ?)', (profile.PRODUCT,))
        db.execute('PRAGMA user_version=3')
    initialize(path, profile.PRODUCT)
    with closing(connect(path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'mix_buckets', 'mix_orders', 'mix_movements'} <= names
        # Historical structures are untouched.
        assert {'archive_stock', 'archive_movements', 'reviews'} <= names


def main():
    with TemporaryDirectory() as tmp:
        v3_migration(tmp)
    for fn in (admission_guards, probe_rework_only, locked_flow, pool_flow,
               branch_a, branch_a_locked, branch_a_relock, branch_b, history_and_tickets):
        scenario(fn)
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / 'mix.sqlite3'
        initialize(db, profile.PRODUCT)
        with ThreadingHTTPServer(('127.0.0.1', 0), QuietEndpoint) as server:
            server.service = Service(db, profile)
            worker = Thread(target=server.serve_forever)
            worker.start()
            try:
                concurrent_booking(server.server_port)
            finally:
                server.shutdown()
                worker.join()
    print('Mix ledger: admissions, commitments, weighing and rejection checks passed')


if __name__ == '__main__':
    main()
