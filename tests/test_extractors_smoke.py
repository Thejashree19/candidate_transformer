"""Quick smoke test for all extractors — run from d:\\8F with: python tests/test_extractors_smoke.py"""
import sys
sys.path.insert(0, ".")

from src.extractors import BaseExtractor, CSVExtractor, ATSExtractor, GitHubExtractor, NotesExtractor
from src.models import SourceEnvelope, SourceType, SourceStatus

def test_csv():
    e = SourceEnvelope(source_type=SourceType.RECRUITER_CSV, raw_data="name,email,skills\nAlice,alice@test.com,Python;Go\n,,,")
    c = CSVExtractor().safe_extract(e)
    assert len(c) == 1 and c[0].full_name == "Alice" and len(c[0].skills) == 2
    print("  CSV  ✓")

def test_ats():
    data = {"applicant_name": "Bob", "contact_email": "bob@x.com", "tech_stack": ["Java", "K8s"],
            "work_history": [{"employer": "Acme", "role": "SWE", "start_date": "2020-01"}],
            "education_history": [{"school": "MIT", "degree_type": "BS", "major": "CS", "graduation_year": 2019}],
            "social_profiles": [{"type": "linkedin", "url": "https://linkedin.com/in/bob"}]}
    e = SourceEnvelope(source_type=SourceType.ATS_JSON, raw_data=data)
    c = ATSExtractor().safe_extract(e)
    assert len(c) == 1 and c[0].full_name == "Bob" and c[0].linkedin_url == "https://linkedin.com/in/bob"
    print("  ATS  ✓")

def test_github_cached():
    cached = {"profile": {"name": "Octocat", "email": "octo@gh.com", "bio": "I build things",
                           "location": "SF", "company": "GitHub", "blog": "octocat.dev", "html_url": "https://github.com/octocat"},
              "repos": [{"language": "Python", "topics": ["ml"]}, {"language": "Go", "topics": []}]}
    e = SourceEnvelope(source_type=SourceType.GITHUB, raw_data=cached)
    c = GitHubExtractor().safe_extract(e)
    assert len(c) == 1 and c[0].full_name == "Octocat" and len(c[0].skills) == 3
    print("  GitHub (cached)  ✓")

def test_notes():
    text = "Candidate: Jane Doe\njane@example.com\nPhone: 555-123-4567\nSkills: Python, React, AWS\nWorks at Google\n10 years of experience\nBS in CS from Stanford\nBased in San Francisco"
    e = SourceEnvelope(source_type=SourceType.RECRUITER_NOTES, raw_data=text)
    c = NotesExtractor().safe_extract(e)
    assert len(c) == 1 and c[0].full_name == "Jane Doe" and c[0].emails == ["jane@example.com"]
    print("  Notes  ✓")

def test_safe_extract_on_bad_status():
    e = SourceEnvelope(source_type=SourceType.RECRUITER_CSV, raw_data="garbage", status=SourceStatus.MALFORMED)
    assert CSVExtractor().safe_extract(e) == []
    print("  safe_extract (bad status)  ✓")

if __name__ == "__main__":
    print("Running extractor smoke tests...")
    test_csv()
    test_ats()
    test_github_cached()
    test_notes()
    test_safe_extract_on_bad_status()
    print("All tests passed!")
