from openai import OpenAI
import json
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def parse_cv_with_ai(raw_text: str):
    prompt = f"""
	You are an expert CV parser.
	
	Your job is to read the CV text and return a single JSON object that captures
	all relevant structured information. You MUST use the exact schema below.
	
	Very important rules:
	- ALWAYS fill fields when the information is present in the CV.
	- DO NOT leave fields blank if the CV text contains the data.
	- If you truly cannot find a value, leave that field as an empty string "" or empty list [].
	- Never change the field names or structure.
	- Do NOT add extra top-level fields.
	- Return JSON ONLY, no explanations, no markdown, no comments.
	
	Schema (example with empty values you must overwrite with real data where possible):
	
	{{
	  "first_name": "",
	  "last_name": "",
	  "email": "",
	  "phone": "",
	  "address": "",
	
	  "summary": "",
	  "skills": [],
	  "job_titles": [],
	
	  "experience": [
	    {{
	      "company": "",
	      "role": "",
	      "start_date": "",
	      "end_date": "",
	      "description": ""
	    }}
	  ],
	
	  "education": [
	    {{
	      "degree": "",
	      "institution": "",
	      "graduation_year": ""
	    }}
	  ],
	
	  "certifications": [],
	  "languages": [],
	
	  "additional_details": {{
	    "publications": [],
	    "github": "",
	    "linkedin": "",
	    "portfolio": "",
	    "thesis": "",
	    "awards": [],
	    "volunteering": [],
	    "interests": [],
	    "other": ""
	  }}
	}}
	
	Extraction rules and hints:
	- first_name / last_name: derive from the candidate name at the top of the CV.
	- email: any email address in the contact section.
	- phone: any phone or mobile number (normalise spacing, but keep the digits).
	- address: postal address or city + region if full address not given.
	- summary: short paragraph describing the candidate overall (use “Profile” or similar section if present).
	- skills: list of skills, tools, and key competencies (deduplicate).
	- job_titles: list of distinct job titles held (e.g. "Sales Assistant", "Data Analyst").
	- experience: each past job with company, role, start_date, end_date, and a concise description.
	- education: each course/degree with institution and year (or best estimate of graduation_year).
	- certifications: list of any courses, certificates, or professional credentials.
	- languages: list of languages mentioned.
	- additional_details: fill lists and strings where matching info exists (awards, publications, interests, etc.).
	
	Now parse the following CV text and output ONLY the JSON object that matches the schema above.
	
	CV TEXT:
	{raw_text}
	"""

    response = client.responses.create(
        model="gpt-4.1",  # or "gpt-4.1-mini" for cheaper parsing
        input=prompt,
        max_output_tokens=2000,
    )

    output = response.output_text

    # Sometimes models wrap JSON in ```json ``` fences - strip them if present
    cleaned = output.strip()
    if cleaned.startswith("```"):
        # remove leading ```... and trailing ```
        cleaned = cleaned.strip("`")
        # if there's a 'json' language tag, remove it
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # As a last resort, return an empty but valid structure so the app doesn’t break
        return {
            "first_name": "",
            "last_name": "",
            "email": "",
            "phone": "",
            "address": "",
            "summary": "",
            "skills": [],
            "job_titles": [],
            "experience": [],
            "education": [],
            "certifications": [],
            "languages": [],
            "additional_details": {
                "publications": [],
                "github": "",
                "linkedin": "",
                "portfolio": "",
                "thesis": "",
                "awards": [],
                "volunteering": [],
                "interests": [],
                "other": ""
            }
        }

