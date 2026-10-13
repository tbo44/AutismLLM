-- Apply explicitly to the chosen PostgreSQL database before enabling the backend.
-- No other application data is moved or modified.
CREATE TABLE IF NOT EXISTS public.maya_test_alert_attempts (
    key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('pending', 'uncertain', 'completed', 'archived')),
    result JSONB,
    completed_at DOUBLE PRECISION,
    CHECK (state != 'completed' OR (result IS NOT NULL AND completed_at IS NOT NULL)),
    CHECK (state != 'archived' OR result IS NOT NULL)
);
