CREATE TABLE archive_custody_rounds (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    dataset TEXT NOT NULL REFERENCES archive_stock(dataset),
    cabinet TEXT NOT NULL,
    ticket TEXT NOT NULL,
    request TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open', 'closed')),
    created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset, ticket)
);
CREATE TABLE archive_custody_events (
    seq INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL REFERENCES archive_stock(dataset),
    round TEXT NOT NULL REFERENCES archive_custody_rounds(id),
    ticket TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('dispatch', 'issue', 'return', 'verify')),
    seal TEXT,
    order_name TEXT,
    mass_g INTEGER,
    request TEXT NOT NULL,
    recorded TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset, ticket)
);
CREATE INDEX archive_custody_events_by_round ON archive_custody_events(dataset, round, seq);
CREATE TABLE archive_custody_packages (
    dataset TEXT NOT NULL,
    round TEXT NOT NULL REFERENCES archive_custody_rounds(id),
    seal TEXT NOT NULL,
    cell TEXT NOT NULL,
    mass_g INTEGER NOT NULL CHECK(mass_g > 0),
    state TEXT NOT NULL DEFAULT 'held' CHECK(state IN ('held', 'issued', 'pending', 'verified', 'restored')),
    order_name TEXT,
    voucher_seq INTEGER REFERENCES archive_custody_events(seq),
    scan_seq INTEGER REFERENCES archive_custody_events(seq),
    restored_seq INTEGER REFERENCES archive_custody_events(seq),
    PRIMARY KEY(dataset, seal)
);
CREATE INDEX archive_custody_packages_by_round ON archive_custody_packages(round);
CREATE TABLE archive_custody_orders (
    round TEXT NOT NULL REFERENCES archive_custody_rounds(id),
    order_name TEXT NOT NULL,
    cell TEXT NOT NULL,
    mass_g INTEGER NOT NULL CHECK(mass_g > 0),
    kind TEXT NOT NULL CHECK(kind IN ('guaranteed', 'standby')),
    acceptable INTEGER NOT NULL CHECK(acceptable IN (0, 1)),
    PRIMARY KEY(round, order_name)
);
