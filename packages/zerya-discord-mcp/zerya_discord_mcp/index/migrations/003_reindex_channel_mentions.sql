-- Channel mentions are now stored as <#id> and resolved per reader when rendering (no hidden channel names in
-- the index). Restart the backward backfill so every stored message is rewritten in the new form.
UPDATE cursors SET oldest_id = NULL, complete = 0;
