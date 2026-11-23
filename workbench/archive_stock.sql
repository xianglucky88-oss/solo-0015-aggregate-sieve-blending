CREATE TABLE archive_stock (
    dataset TEXT PRIMARY KEY NOT NULL,
    generation INTEGER NOT NULL,
    run TEXT NOT NULL REFERENCES runs(id),
    opening TEXT NOT NULL,
    opened TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(dataset, generation) REFERENCES publication_events(dataset, generation)
);
CREATE TABLE archive_movements (
    seq INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL REFERENCES archive_stock(dataset),
    ticket TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('issue', 'return', 'correction')),
    issue_ticket TEXT,
    cell TEXT NOT NULL,
    order_name TEXT NOT NULL,
    delta_g INTEGER NOT NULL CHECK(delta_g <> 0),
    request TEXT NOT NULL,
    recorded TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset, ticket),
    FOREIGN KEY(dataset, issue_ticket) REFERENCES archive_movements(dataset, ticket),
    CHECK((kind = 'issue' AND issue_ticket IS NULL AND delta_g > 0) OR
          (kind = 'return' AND issue_ticket IS NOT NULL AND delta_g < 0) OR
          (kind = 'correction' AND issue_ticket IS NOT NULL))
);
CREATE INDEX archive_movements_by_issue ON archive_movements(dataset, issue_ticket, seq);
CREATE INDEX archive_movements_by_cell ON archive_movements(dataset, cell, order_name);
CREATE INDEX archive_movements_by_order ON archive_movements(dataset, order_name, cell);
