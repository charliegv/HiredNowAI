from openai import AsyncOpenAI
import json
import markdown2
from docx import Document
import tempfile
import os

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def generate_custom_cv(base_cv_text, job_text, user):

    prompt = f"""
    Rewrite the CV into an improved JSON-structured CV, optimised for the job description.
    
    Rules:
    - Keep factual details exactly the same
    - Highlight relevant experience using strong action verbs
    - Output ONLY valid JSON (no explanation text)
    
    JSON keys:
    first_name, last_name, email, phone, address, summary, skills,
    job_titles, experience, education, certifications, languages, additional_details.
    
    Base CV:
    {base_cv_text}
    
    Job Description:
    {job_text}
    
    User:
    {user.get("first_name", "")} {user.get("last_name", "")}
    """

    # ✔ Correct new async usage
    response = await client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.25
    )

    content = response.choices[0].message.content

    # Strip markdown fences if present
    cleaned = content.replace("```json", "").replace("```", "").strip()

    # Convert JSON string → dict
    cv_json = json.loads(cleaned)

    # ---- Build DOCX ----
    doc = Document()
    doc.add_heading(f"{cv_json['first_name']} {cv_json['last_name']}", level=1)
    doc.add_paragraph(cv_json.get("summary", ""))

    doc.add_heading("Skills", level=2)
    for s in cv_json.get("skills", []):
        doc.add_paragraph(f"• {s}")

    doc.add_heading("Experience", level=2)
    for exp in cv_json.get("experience", []):
        role = exp.get("role") or exp.get("title") or "Experience"
        company = exp.get("company", "Unknown Company")
        start = exp.get("start_date", "")
        end = exp.get("end_date", "")
        desc = exp.get("description", "")

        doc.add_paragraph(f"{role} - {company} ({start} - {end})")
        if desc:
            doc.add_paragraph(desc)

    tmp_path = tempfile.mktemp(suffix=".docx")
    doc.save(tmp_path)

    return cv_json, tmp_path
