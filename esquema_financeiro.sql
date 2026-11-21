CREATE TABLE schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (typeof(schema_version) = 'integer'),
    schema_sha256 TEXT NOT NULL CHECK (
        length(schema_sha256) = 64 AND schema_sha256 NOT GLOB '*[^0-9a-f]*'
    )
);

CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    cpf TEXT NOT NULL UNIQUE
        CHECK (length(cpf) = 11 AND cpf NOT GLOB '*[^0-9]*'),
    nome TEXT NOT NULL CHECK (length(trim(nome)) > 0),
    senha_hash TEXT,
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (typeof(ativo) = 'integer' AND ativo IN (0, 1)),
    credential_version INTEGER NOT NULL DEFAULT 1 CHECK (
        typeof(credential_version) = 'integer' AND credential_version >= 1
    ),
    CHECK (
        senha_hash IS NULL
        OR (
            length(senha_hash) BETWEEN 80 AND 256
            AND instr(senha_hash, '$') BETWEEN 2 AND 64
            AND instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') BETWEEN 9 AND 65
            AND substr(
                senha_hash,
                instr(senha_hash, '$') + 1,
                instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') - 1
            ) NOT GLOB '*[^A-Za-z0-9]*'
            AND length(substr(
                senha_hash,
                instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1
            )) BETWEEN 40 AND 128
            AND substr(
                senha_hash,
                instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1
            ) NOT GLOB '*[^0-9a-f]*'
            AND (
                (
                    substr(senha_hash, 1, instr(senha_hash, '$') - 1) GLOB 'scrypt:[0-9]*:[0-9]*:[0-9]*'
                    AND substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8)
                        NOT GLOB '*[^0-9:]*'
                    AND (length(substr(senha_hash, 1, instr(senha_hash, '$') - 1))
                         - length(replace(substr(senha_hash, 1, instr(senha_hash, '$') - 1), ':', ''))) = 3
                    AND instr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), '::') = 0
                    AND CAST(substr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8),
                        1, instr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8), ':') - 1)
                        AS INTEGER) BETWEEN 16384 AND 262144
                    AND CAST(substr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8),
                        instr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8), ':') + 1,
                        instr(substr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8),
                            instr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8), ':') + 1), ':') - 1)
                        AS INTEGER) = 8
                    AND CAST(substr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8),
                        instr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8), ':') + 1 +
                        instr(substr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8),
                            instr(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 8), ':') + 1), ':'))
                        AS INTEGER) BETWEEN 1 AND 2
                )
                OR (
                    substr(senha_hash, 1, instr(senha_hash, '$') - 1) GLOB 'pbkdf2:sha1:[0-9]*'
                    AND CAST(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 13) AS INTEGER) BETWEEN 600000 AND 2000000
                    AND substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 13) NOT GLOB '*[^0-9]*'
                    AND length(substr(senha_hash, instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1)) = 40
                )
                OR (
                    substr(senha_hash, 1, instr(senha_hash, '$') - 1) GLOB 'pbkdf2:sha224:[0-9]*'
                    AND CAST(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) AS INTEGER) BETWEEN 600000 AND 2000000
                    AND substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) NOT GLOB '*[^0-9]*'
                    AND length(substr(senha_hash, instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1)) = 56
                )
                OR (
                    substr(senha_hash, 1, instr(senha_hash, '$') - 1) GLOB 'pbkdf2:sha256:[0-9]*'
                    AND CAST(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) AS INTEGER) BETWEEN 600000 AND 2000000
                    AND substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) NOT GLOB '*[^0-9]*'
                    AND length(substr(senha_hash, instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1)) = 64
                )
                OR (
                    substr(senha_hash, 1, instr(senha_hash, '$') - 1) GLOB 'pbkdf2:sha384:[0-9]*'
                    AND CAST(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) AS INTEGER) BETWEEN 600000 AND 2000000
                    AND substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) NOT GLOB '*[^0-9]*'
                    AND length(substr(senha_hash, instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1)) = 96
                )
                OR (
                    substr(senha_hash, 1, instr(senha_hash, '$') - 1) GLOB 'pbkdf2:sha512:[0-9]*'
                    AND CAST(substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) AS INTEGER) BETWEEN 600000 AND 2000000
                    AND substr(substr(senha_hash, 1, instr(senha_hash, '$') - 1), 15) NOT GLOB '*[^0-9]*'
                    AND length(substr(senha_hash, instr(senha_hash, '$') + instr(substr(senha_hash, instr(senha_hash, '$') + 1), '$') + 1)) = 128
                )
            )
        )
    )
);

CREATE TABLE credential_audit_events (
    event_id INTEGER PRIMARY KEY,
    actor_user_id INTEGER NOT NULL
        REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    account_user_id INTEGER NOT NULL
        REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action = 'password_change'),
    occurred_at TEXT NOT NULL,
    protocol TEXT NOT NULL CHECK (length(trim(protocol)) BETWEEN 3 AND 128),
    result TEXT NOT NULL CHECK (result = 'success'),
    credential_version_before INTEGER NOT NULL CHECK (
        typeof(credential_version_before) = 'integer' AND credential_version_before >= 1
    ),
    credential_version_after INTEGER NOT NULL CHECK (
        typeof(credential_version_after) = 'integer'
        AND credential_version_after = credential_version_before + 1
    )
);
CREATE INDEX idx_credential_audit_account_time
    ON credential_audit_events(account_user_id, occurred_at DESC, event_id DESC);

-- Saldos começam em zero e são atualizados pelo trigger do razão financeiro.
CREATE TABLE accounts (
    user_id INTEGER PRIMARY KEY
        REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (
        typeof(balance_cents) = 'integer'
        AND balance_cents >= 0
        AND balance_cents <= 100000000000
    ),
    last_transaction_id INTEGER NOT NULL DEFAULT 0 CHECK (
        typeof(last_transaction_id) = 'integer' AND last_transaction_id >= 0
    )
);

CREATE TABLE financial_transactions (
    transaction_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL
        REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_type TEXT NOT NULL
        CHECK (operation_type IN (
            'opening_balance', 'deposit', 'bet_win', 'bet_loss',
            'slot_win', 'slot_pair', 'slot_loss'
        )),
    delta_cents INTEGER NOT NULL CHECK (
        typeof(delta_cents) = 'integer'
        AND delta_cents >= -100000000000
        AND delta_cents <= 100000000000
    ),
    occurred_at TEXT NOT NULL,
    -- source_row é a posição original no arquivo da fonte deste lote, iniciada em zero.
    source_file TEXT,
    source_row INTEGER,
    amount_cents INTEGER,
    wager_cents INTEGER,
    chosen_number INTEGER,
    drawn_number INTEGER,
    outcome TEXT,
    slot_symbol_1 INTEGER,
    slot_symbol_2 INTEGER,
    slot_symbol_3 INTEGER,
    slot_multiplier INTEGER,
    slot_payout_symbol INTEGER,
    CHECK (
        (source_file IS NULL AND source_row IS NULL)
        OR (source_file IS NOT NULL AND source_row IS NOT NULL
            AND typeof(source_row) = 'integer' AND source_row >= 0)
    ),
    CHECK (amount_cents IS NULL OR typeof(amount_cents) = 'integer'),
    CHECK (wager_cents IS NULL OR typeof(wager_cents) = 'integer'),
    CHECK (chosen_number IS NULL OR typeof(chosen_number) = 'integer'),
    CHECK (drawn_number IS NULL OR typeof(drawn_number) = 'integer'),
    CHECK (slot_symbol_1 IS NULL OR typeof(slot_symbol_1) = 'integer'),
    CHECK (slot_symbol_2 IS NULL OR typeof(slot_symbol_2) = 'integer'),
    CHECK (slot_symbol_3 IS NULL OR typeof(slot_symbol_3) = 'integer'),
    CHECK (slot_payout_symbol IS NULL OR typeof(slot_payout_symbol) = 'integer'),
    CHECK (
        (operation_type = 'opening_balance'
            AND delta_cents >= 0
            AND amount_cents IS NULL AND wager_cents IS NULL
            AND chosen_number IS NULL AND drawn_number IS NULL AND outcome IS NULL
            AND slot_symbol_1 IS NULL AND slot_symbol_2 IS NULL AND slot_symbol_3 IS NULL)
        OR
        (operation_type = 'deposit'
            AND delta_cents > 0 AND amount_cents IS NOT NULL
            AND amount_cents = delta_cents
            AND amount_cents <= 100000000000
            AND wager_cents IS NULL AND chosen_number IS NULL
            AND drawn_number IS NULL AND outcome IS NULL
            AND slot_symbol_1 IS NULL AND slot_symbol_2 IS NULL AND slot_symbol_3 IS NULL)
        OR
        (operation_type = 'bet_win'
            AND amount_cents IS NULL
            AND wager_cents IS NOT NULL
            AND typeof(wager_cents) = 'integer'
            AND chosen_number IS NOT NULL
            AND typeof(chosen_number) = 'integer'
            AND drawn_number IS NOT NULL
            AND typeof(drawn_number) = 'integer'
            AND outcome IS NOT NULL
            AND wager_cents BETWEEN 1 AND 100000000000
            AND chosen_number BETWEEN 0 AND 10 AND drawn_number = chosen_number
            AND outcome = 'win' AND delta_cents = wager_cents * 10
            AND slot_symbol_1 IS NULL AND slot_symbol_2 IS NULL AND slot_symbol_3 IS NULL)
        OR
        (operation_type = 'bet_loss'
            AND amount_cents IS NULL
            AND wager_cents IS NOT NULL
            AND typeof(wager_cents) = 'integer'
            AND chosen_number IS NOT NULL
            AND typeof(chosen_number) = 'integer'
            AND drawn_number IS NOT NULL
            AND typeof(drawn_number) = 'integer'
            AND outcome IS NOT NULL
            AND wager_cents BETWEEN 1 AND 100000000000
            AND chosen_number BETWEEN 0 AND 10 AND drawn_number BETWEEN 0 AND 10
            AND drawn_number <> chosen_number
            AND outcome = 'loss' AND delta_cents = -wager_cents
            AND slot_symbol_1 IS NULL AND slot_symbol_2 IS NULL AND slot_symbol_3 IS NULL)
        OR
        (operation_type = 'slot_win'
            AND amount_cents IS NULL AND wager_cents IS NOT NULL
            AND typeof(wager_cents) = 'integer'
            AND wager_cents BETWEEN 1 AND 100000000000
            AND chosen_number IS NULL AND drawn_number IS NULL
            AND outcome = 'triple' AND delta_cents = wager_cents * slot_multiplier
            AND slot_symbol_1 BETWEEN 0 AND 10
            AND slot_symbol_2 BETWEEN 0 AND 10
            AND slot_symbol_3 BETWEEN 0 AND 10
            AND typeof(slot_symbol_1) = 'integer'
            AND typeof(slot_symbol_2) = 'integer'
            AND typeof(slot_symbol_3) = 'integer'
            AND slot_symbol_1 = slot_symbol_2 AND slot_symbol_2 = slot_symbol_3
            AND slot_payout_symbol = slot_symbol_1
            AND typeof(slot_multiplier) = 'integer'
            AND slot_multiplier = CASE slot_symbol_1
                WHEN 0 THEN 2 WHEN 1 THEN 3 WHEN 2 THEN 5 WHEN 3 THEN 8
                WHEN 4 THEN 10 WHEN 5 THEN 15 WHEN 6 THEN 20 WHEN 7 THEN 30
                WHEN 8 THEN 40 WHEN 9 THEN 60 WHEN 10 THEN 100 END)
        OR
        (operation_type = 'slot_pair'
            AND amount_cents IS NULL AND wager_cents IS NOT NULL
            AND typeof(wager_cents) = 'integer'
            AND wager_cents BETWEEN 1 AND 100000000000
            AND chosen_number IS NULL AND drawn_number IS NULL
            AND outcome = 'pair'
            AND delta_cents = (2 * wager_cents * slot_multiplier + 3) / 6
            AND slot_symbol_1 BETWEEN 0 AND 10 AND slot_symbol_2 BETWEEN 0 AND 10
            AND slot_symbol_3 BETWEEN 0 AND 10
            AND typeof(slot_symbol_1) = 'integer'
            AND typeof(slot_symbol_2) = 'integer'
            AND typeof(slot_symbol_3) = 'integer'
            AND ((slot_symbol_1 = slot_symbol_2) + (slot_symbol_1 = slot_symbol_3)
                 + (slot_symbol_2 = slot_symbol_3)) = 1
            AND (slot_payout_symbol = slot_symbol_1 OR slot_payout_symbol = slot_symbol_2
                 OR slot_payout_symbol = slot_symbol_3)
            AND ((slot_payout_symbol = slot_symbol_1 AND slot_symbol_1 = slot_symbol_2)
                 OR (slot_payout_symbol = slot_symbol_1 AND slot_symbol_1 = slot_symbol_3)
                 OR (slot_payout_symbol = slot_symbol_2 AND slot_symbol_2 = slot_symbol_3))
            AND typeof(slot_multiplier) = 'integer'
            AND slot_multiplier = CASE slot_payout_symbol
                WHEN 0 THEN 2 WHEN 1 THEN 3 WHEN 2 THEN 5 WHEN 3 THEN 8
                WHEN 4 THEN 10 WHEN 5 THEN 15 WHEN 6 THEN 20 WHEN 7 THEN 30
                WHEN 8 THEN 40 WHEN 9 THEN 60 WHEN 10 THEN 100 END)
        OR
        (operation_type = 'slot_loss'
            AND amount_cents IS NULL AND wager_cents IS NOT NULL
            AND typeof(wager_cents) = 'integer'
            AND wager_cents BETWEEN 1 AND 100000000000
            AND chosen_number IS NULL AND drawn_number IS NULL
            AND outcome = 'loss' AND delta_cents = -wager_cents
            AND slot_symbol_1 BETWEEN 0 AND 10 AND slot_symbol_2 BETWEEN 0 AND 10
            AND slot_symbol_3 BETWEEN 0 AND 10
            AND typeof(slot_symbol_1) = 'integer'
            AND typeof(slot_symbol_2) = 'integer'
            AND typeof(slot_symbol_3) = 'integer'
            AND slot_symbol_1 <> slot_symbol_2 AND slot_symbol_1 <> slot_symbol_3
            AND slot_symbol_2 <> slot_symbol_3
            AND slot_multiplier IS NULL AND slot_payout_symbol IS NULL)
    ),
    CHECK (
        (operation_type IN ('slot_win', 'slot_pair') AND slot_multiplier IS NOT NULL
            AND slot_payout_symbol BETWEEN 0 AND 10)
        OR (operation_type IN ('slot_loss') AND slot_multiplier IS NULL
            AND slot_payout_symbol IS NULL)
        OR (operation_type NOT IN ('slot_win', 'slot_pair', 'slot_loss')
            AND slot_multiplier IS NULL AND slot_payout_symbol IS NULL)
    )
);

CREATE UNIQUE INDEX idx_financial_one_opening_per_user
    ON financial_transactions(user_id) WHERE operation_type = 'opening_balance';
CREATE INDEX idx_financial_transactions_user_time
    ON financial_transactions(user_id, occurred_at DESC, transaction_id DESC);
CREATE INDEX idx_financial_transactions_type_time
    ON financial_transactions(operation_type, occurred_at DESC, transaction_id DESC);

-- Views mantêm consultas separadas sem duplicar a fonte financeira dos dados.
CREATE VIEW deposits AS
    SELECT transaction_id, amount_cents, occurred_at
    FROM financial_transactions WHERE operation_type = 'deposit';

CREATE VIEW bets AS
    SELECT transaction_id, wager_cents, chosen_number, drawn_number, outcome,
           CASE WHEN operation_type IN ('slot_win', 'slot_pair', 'slot_loss') THEN 'slot' ELSE 'roulette' END
               AS game_type,
           slot_symbol_1, slot_symbol_2, slot_symbol_3, slot_multiplier,
           slot_payout_symbol, outcome AS slot_outcome,
           delta_cents AS balance_delta_cents, occurred_at
    FROM financial_transactions
    WHERE operation_type IN ('bet_win', 'bet_loss', 'slot_win', 'slot_pair', 'slot_loss');

CREATE TABLE legacy_history (
    legacy_id INTEGER PRIMARY KEY,
    bundle_hash TEXT NOT NULL
        REFERENCES migration_batches(bundle_hash) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_file TEXT NOT NULL
        CHECK (source_file IN ('depositos.json', 'apostas.json')),
    source_row INTEGER NOT NULL CHECK (
        typeof(source_row) = 'integer' AND source_row >= 0
    ),
    history_type TEXT NOT NULL CHECK (history_type IN ('deposit', 'bet')),
    user_id INTEGER NOT NULL
        REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    original_data_json TEXT NOT NULL,
    original_datetime TEXT,
    record_hash TEXT NOT NULL CHECK (
        length(record_hash) = 64 AND record_hash NOT GLOB '*[^0-9a-f]*'
    ),
    UNIQUE(bundle_hash, source_file, source_row),
    UNIQUE(source_file, record_hash)
);

CREATE INDEX idx_legacy_history_user
    ON legacy_history(user_id, source_file, source_row);

CREATE TABLE idempotency_keys (
    key_hash TEXT PRIMARY KEY CHECK (
        length(key_hash) = 64 AND key_hash NOT GLOB '*[^0-9a-f]*'
    ),
    user_id INTEGER NOT NULL
        REFERENCES users(user_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    operation_type TEXT NOT NULL CHECK (operation_type IN ('deposit', 'bet', 'slot')),
    state TEXT NOT NULL CHECK (state IN ('processing', 'completed')),
    transaction_id INTEGER UNIQUE
        REFERENCES financial_transactions(transaction_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (state = 'processing' AND transaction_id IS NULL AND result_json IS NULL)
        OR
        (state = 'completed' AND transaction_id IS NOT NULL AND result_json IS NOT NULL)
    )
);

CREATE INDEX idx_idempotency_user_time
    ON idempotency_keys(user_id, created_at DESC);

CREATE TABLE migration_batches (
    bundle_hash TEXT PRIMARY KEY CHECK (
        length(bundle_hash) = 64 AND bundle_hash NOT GLOB '*[^0-9a-f]*'
    ),
    imported_at TEXT NOT NULL,
    account_count INTEGER NOT NULL CHECK (typeof(account_count) = 'integer' AND account_count >= 0),
    deposit_history_count INTEGER NOT NULL CHECK (
        typeof(deposit_history_count) = 'integer' AND deposit_history_count >= 0
    ),
    bet_history_count INTEGER NOT NULL CHECK (
        typeof(bet_history_count) = 'integer' AND bet_history_count >= 0
    )
);

CREATE TRIGGER financial_transaction_balance_guard
BEFORE INSERT ON financial_transactions
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM accounts WHERE user_id = NEW.user_id)
        THEN RAISE(ABORT, 'financial transaction requires an account')
        WHEN (SELECT balance_cents + NEW.delta_cents FROM accounts WHERE user_id = NEW.user_id)
             NOT BETWEEN 0 AND 100000000000
        THEN RAISE(ABORT, 'financial transaction exceeds account balance limits')
        WHEN NEW.operation_type = 'deposit' AND NOT EXISTS (
            SELECT 1 FROM idempotency_keys
            WHERE user_id = NEW.user_id AND operation_type = 'deposit' AND state = 'processing'
        )
        THEN RAISE(ABORT, 'deposit requires a processing idempotency key')
        WHEN NEW.operation_type IN ('bet_win', 'bet_loss') AND NOT EXISTS (
            SELECT 1 FROM idempotency_keys
            WHERE user_id = NEW.user_id AND operation_type = 'bet' AND state = 'processing'
        )
        THEN RAISE(ABORT, 'bet requires a processing idempotency key')
        WHEN NEW.operation_type IN ('bet_win', 'bet_loss') AND (
            (SELECT balance_cents FROM accounts WHERE user_id = NEW.user_id) < NEW.wager_cents
            OR
            (SELECT balance_cents + NEW.wager_cents * 10
             FROM accounts WHERE user_id = NEW.user_id) > 100000000000
        )
        THEN RAISE(ABORT, 'bet lacks loss coverage or maximum prize capacity')
        WHEN NEW.operation_type IN ('slot_win', 'slot_pair', 'slot_loss') AND NOT EXISTS (
            SELECT 1 FROM idempotency_keys
            WHERE user_id = NEW.user_id AND operation_type = 'slot' AND state = 'processing'
        )
        THEN RAISE(ABORT, 'slot requires a processing idempotency key')
        WHEN NEW.operation_type IN ('slot_win', 'slot_pair', 'slot_loss') AND (
            (SELECT balance_cents FROM accounts WHERE user_id = NEW.user_id) < NEW.wager_cents
            OR
            (SELECT balance_cents + NEW.wager_cents * 100
             FROM accounts WHERE user_id = NEW.user_id) > 100000000000
        )
        THEN RAISE(ABORT, 'slot lacks wager coverage or maximum prize capacity')
    END;
END;

CREATE TRIGGER accounts_start_at_zero
BEFORE INSERT ON accounts
WHEN NEW.balance_cents <> 0 OR NEW.last_transaction_id <> 0
BEGIN
    SELECT RAISE(ABORT, 'accounts must start at zero');
END;

CREATE TRIGGER financial_transaction_updates_account
AFTER INSERT ON financial_transactions
BEGIN
    UPDATE accounts
    SET balance_cents = balance_cents + NEW.delta_cents,
        last_transaction_id = NEW.transaction_id
    WHERE user_id = NEW.user_id;
END;

CREATE TRIGGER account_balance_guard
BEFORE UPDATE OF balance_cents, last_transaction_id ON accounts
WHEN NEW.last_transaction_id <= OLD.last_transaction_id
  OR NOT EXISTS (
      SELECT 1 FROM financial_transactions AS ft
      WHERE ft.transaction_id = NEW.last_transaction_id
        AND ft.user_id = OLD.user_id
        AND ft.transaction_id > OLD.last_transaction_id
        AND ft.delta_cents = NEW.balance_cents - OLD.balance_cents
        AND ft.transaction_id = (
            SELECT MAX(latest.transaction_id)
            FROM financial_transactions AS latest WHERE latest.user_id = OLD.user_id
        )
  )
BEGIN
    SELECT RAISE(ABORT, 'account balance must follow the financial ledger');
END;

CREATE TRIGGER account_identity_is_immutable
BEFORE UPDATE OF user_id ON accounts
WHEN NEW.user_id <> OLD.user_id
BEGIN
    SELECT RAISE(ABORT, 'account identity is immutable');
END;

CREATE TRIGGER users_credential_version_guard
BEFORE UPDATE OF senha_hash, credential_version ON users
WHEN (
    NEW.senha_hash IS NOT OLD.senha_hash
    AND NEW.credential_version <> OLD.credential_version + 1
) OR (
    NEW.senha_hash IS OLD.senha_hash
    AND NEW.credential_version <> OLD.credential_version
)
BEGIN
    SELECT RAISE(ABORT, 'credential version must increment with password hash changes');
END;

CREATE TRIGGER credential_audit_insert_guard
BEFORE INSERT ON credential_audit_events
WHEN NEW.result <> 'success'
  OR NEW.action <> 'password_change'
  OR NEW.actor_user_id <> NEW.account_user_id
  OR NOT EXISTS (
      SELECT 1 FROM users
      WHERE user_id = NEW.account_user_id
        AND ativo = 1
        AND credential_version = NEW.credential_version_after
  )
BEGIN
    SELECT RAISE(ABORT, 'credential audit event does not match a successful self password change');
END;

CREATE TRIGGER users_password_change_audit
AFTER UPDATE OF senha_hash ON users
WHEN NEW.senha_hash IS NOT OLD.senha_hash
BEGIN
    INSERT INTO credential_audit_events
        (actor_user_id, account_user_id, action, occurred_at, protocol, result,
         credential_version_before, credential_version_after)
    VALUES
        (NEW.user_id, NEW.user_id, 'password_change',
         strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'self-service', 'success',
         OLD.credential_version, NEW.credential_version);
END;

CREATE TRIGGER credential_audit_is_immutable_update
BEFORE UPDATE ON credential_audit_events
BEGIN
    SELECT RAISE(ABORT, 'credential audit events are append-only');
END;

CREATE TRIGGER credential_audit_is_immutable_delete
BEFORE DELETE ON credential_audit_events
BEGIN
    SELECT RAISE(ABORT, 'credential audit events are append-only');
END;

CREATE TRIGGER financial_transactions_are_immutable_update
BEFORE UPDATE ON financial_transactions
BEGIN
    SELECT RAISE(ABORT, 'financial transactions are immutable');
END;

CREATE TRIGGER financial_transactions_are_immutable_delete
BEFORE DELETE ON financial_transactions
BEGIN
    SELECT RAISE(ABORT, 'financial transactions are immutable');
END;

CREATE TRIGGER accounts_are_immutable_delete
BEFORE DELETE ON accounts
BEGIN
    SELECT RAISE(ABORT, 'accounts are preserved with their financial history');
END;

CREATE TRIGGER idempotency_insert_guard
BEFORE INSERT ON idempotency_keys
WHEN NEW.state <> 'processing'
  OR NEW.transaction_id IS NOT NULL
  OR NEW.result_json IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'idempotency keys must begin in processing state');
END;

CREATE TRIGGER idempotency_completion_guard
BEFORE UPDATE ON idempotency_keys
WHEN OLD.state <> 'processing'
  OR NEW.state <> 'completed'
  OR NEW.key_hash <> OLD.key_hash
  OR NEW.user_id <> OLD.user_id
  OR NEW.operation_type <> OLD.operation_type
  OR NEW.created_at <> OLD.created_at
  OR NOT EXISTS (
      SELECT 1 FROM financial_transactions AS ft
      WHERE ft.transaction_id = NEW.transaction_id
        AND ft.user_id = NEW.user_id
        AND ft.occurred_at = NEW.created_at
        AND (
            (NEW.operation_type = 'deposit' AND ft.operation_type = 'deposit')
            OR
            (NEW.operation_type = 'bet' AND ft.operation_type IN ('bet_win', 'bet_loss'))
            OR
            (NEW.operation_type = 'slot' AND ft.operation_type IN ('slot_win', 'slot_pair', 'slot_loss'))
        )
        AND json_valid(NEW.result_json)
        AND json_extract(NEW.result_json, '$.transaction_id') = ft.transaction_id
        AND json_extract(NEW.result_json, '$.user_id') = ft.user_id
        AND json_extract(NEW.result_json, '$.delta_cents') = ft.delta_cents
        AND json_extract(NEW.result_json, '$.balance_cents') = (
            SELECT balance_cents FROM accounts WHERE user_id = ft.user_id
        )
        AND json_extract(NEW.result_json, '$.amount_cents') =
            CASE WHEN ft.operation_type = 'deposit' THEN ft.amount_cents ELSE ft.wager_cents END
        AND json_extract(NEW.result_json, '$.operation_type') = ft.operation_type
        AND json_extract(NEW.result_json, '$.outcome') IS ft.outcome
        AND json_extract(NEW.result_json, '$.chosen_number') IS ft.chosen_number
        AND json_extract(NEW.result_json, '$.drawn_number') IS ft.drawn_number
        AND json_extract(NEW.result_json, '$.slot_symbols[0]') IS ft.slot_symbol_1
        AND json_extract(NEW.result_json, '$.slot_symbols[1]') IS ft.slot_symbol_2
        AND json_extract(NEW.result_json, '$.slot_symbols[2]') IS ft.slot_symbol_3
        AND json_extract(NEW.result_json, '$.slot_multiplier') IS ft.slot_multiplier
        AND json_extract(NEW.result_json, '$.slot_payout_symbol') IS ft.slot_payout_symbol
  )
BEGIN
    SELECT RAISE(ABORT, 'idempotency completion does not match its financial transaction');
END;

CREATE TRIGGER idempotency_completed_is_immutable
BEFORE DELETE ON idempotency_keys
WHEN OLD.state = 'completed'
BEGIN
    SELECT RAISE(ABORT, 'completed idempotency keys are immutable');
END;

CREATE TRIGGER legacy_history_insert_guard
BEFORE INSERT ON legacy_history
WHEN NOT (
    (NEW.source_file = 'depositos.json' AND NEW.history_type = 'deposit')
    OR (NEW.source_file = 'apostas.json' AND NEW.history_type = 'bet')
)
BEGIN
    SELECT RAISE(ABORT, 'legacy source and history type do not match');
END;

CREATE TRIGGER legacy_history_is_immutable_update
BEFORE UPDATE ON legacy_history
BEGIN
    SELECT RAISE(ABORT, 'legacy history is immutable');
END;

CREATE TRIGGER legacy_history_is_immutable_delete
BEFORE DELETE ON legacy_history
BEGIN
    SELECT RAISE(ABORT, 'legacy history is immutable');
END;
