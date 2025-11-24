CREATE TABLE feeds (
    id SERIAL PRIMARY KEY,
    feed_name TEXT NOT NULL UNIQUE,            -- e.g. "uk", "us", "remote", "greenhouse_apple"
    url TEXT NOT NULL,                         -- S3 key or HTTP URL depending on feed_mode
    feed_mode TEXT NOT NULL DEFAULT 's3',      -- 's3' or 'web'
    feed_format TEXT NOT NULL DEFAULT 'standard', -- future: 'greenhouse', 'lever', etc
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
