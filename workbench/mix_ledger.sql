CREATE TABLE mix_buckets (
    dataset TEXT NOT NULL,
    bucket TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('fresh', 'rework')),
    record TEXT NOT NULL,
    admitted TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(dataset, bucket)
);
CREATE TABLE mix_orders (
    dataset TEXT NOT NULL,
    "order" TEXT NOT NULL,
    dry_g INTEGER NOT NULL CHECK(dry_g > 0),
    envelope TEXT NOT NULL,
    allowed TEXT,
    rework_only INTEGER NOT NULL CHECK(rework_only IN (0, 1)),
    state TEXT NOT NULL CHECK(state IN ('committed', 'weighed', 'rejected')),
    mode TEXT CHECK(mode IS NULL OR mode IN ('locked', 'pool')),
    locked_bucket TEXT,
    reason TEXT,
    opened TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(dataset, "order"),
    CHECK(state = 'rejected' OR mode IS NOT NULL),
    CHECK(mode <> 'locked' OR locked_bucket IS NOT NULL),
    CHECK(state = 'rejected' OR reason IS NULL)
);
CREATE TABLE mix_movements (
    seq INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    ticket TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('admit', 'commit', 'weigh', 'reject')),
    bucket TEXT,
    "order" TEXT NOT NULL,
    mass_g INTEGER NOT NULL CHECK(mass_g > 0),
    request TEXT NOT NULL,
    recorded TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset, ticket),
    CHECK(kind <> 'weigh' OR bucket IS NOT NULL)
);
CREATE INDEX mix_movements_by_order ON mix_movements(dataset, "order", seq);
CREATE INDEX mix_movements_by_bucket ON mix_movements(dataset, bucket, seq);
