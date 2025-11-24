-- public.jobs definition

-- Drop table

-- DROP TABLE public.jobs;

CREATE TABLE public.jobs (
	job_url text NOT NULL,
	title text NOT NULL,
	company text NULL,
	description text NULL,
	city text NOT NULL,
	state text DEFAULT ''::text NOT NULL,
	country text NOT NULL,
	latitude float8 NULL,
	longitude float8 NULL,
	is_remote bool DEFAULT false NULL,
	salary_min int4 NULL,
	salary_max int4 NULL,
	posted_at date NULL,
	scraped_at timestamp DEFAULT now() NULL,
	expires_at date DEFAULT (CURRENT_DATE + '14 days'::interval) NULL,
	source_ats text NULL,
	source_job_id text NOT NULL,
	feed_source text NULL,
	hash text NULL,
	geo public.geography(point, 4326) NULL,
	CONSTRAINT jobs_pkey PRIMARY KEY (job_url, city, state, country, source_job_id)
);
CREATE INDEX idx_jobs_city ON public.jobs USING btree (city);
CREATE INDEX idx_jobs_country ON public.jobs USING btree (country);
CREATE INDEX idx_jobs_fts ON public.jobs USING gin (to_tsvector('english'::regconfig, ((COALESCE(title, ''::text) || ' '::text) || COALESCE(description, ''::text))));
CREATE INDEX idx_jobs_geo ON public.jobs USING gist (geo);
CREATE INDEX idx_jobs_lat ON public.jobs USING btree (latitude);
CREATE INDEX idx_jobs_lon ON public.jobs USING btree (longitude);
CREATE INDEX idx_jobs_remote ON public.jobs USING btree (is_remote);
CREATE INDEX idx_jobs_salary_max ON public.jobs USING btree (salary_max);
CREATE INDEX idx_jobs_salary_min ON public.jobs USING btree (salary_min);
CREATE INDEX idx_jobs_title_trgm ON public.jobs USING gin (title gin_trgm_ops);