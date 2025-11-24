CREATE TABLE matches (
    user_id INT NOT NULL,
    job_url TEXT NOT NULL,
    job_id BIGINT NULL,
    score FLOAT8 NOT NULL,
    is_remote BOOLEAN DEFAULT FALSE,
    matched_at TIMESTAMP DEFAULT NOW(),

    CONSTRAINT matches_pkey PRIMARY KEY (user_id, job_url)
);
