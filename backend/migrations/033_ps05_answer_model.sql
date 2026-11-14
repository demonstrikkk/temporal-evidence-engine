-- PS05 - which model wrote the answer we scanned.
--
-- A verification run knows every model call it made, and nothing about the
-- model that produced the answer in the first place. That is fine for a human
-- pasting a chat answer, and it makes one obvious question unanswerable: of the
-- answers this engine has scanned, how many claims came back `supported` when
-- Mistral wrote them, and how many when something else did?
--
-- Without this join a "compare models" page can only report call cost and
-- latency, and any verdict-quality column would have to be invented. Rather
-- than guess, the scan request now carries the attribution when the caller
-- knows it (the playground does: the gateway returns the resolved model), and
-- every answer recorded before today keeps an empty string - "not recorded",
-- not "unknown model".
--
-- `model` is the model that actually answered (the gateway's `resolved_model`),
-- not the one requested. `provider` is the provider the gateway reported. Both
-- are optional facts, never inferred from the answer text.
--
-- Idempotent and additive: a re-run is a no-op and existing rows read as
-- "attribution not recorded", which is what they are.

ALTER TABLE tev.answers
    ADD COLUMN IF NOT EXISTS model text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT '';

COMMENT ON COLUMN tev.answers.model IS
    'The model that answered (gateway resolved_model), when the caller knew it. Empty means not recorded.';
COMMENT ON COLUMN tev.answers.provider IS
    'The provider that served the answer, as the caller reported it. Empty means not recorded.';
