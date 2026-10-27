# Contributing

Thanks for helping! The most useful contribution is usually a **new ATS adapter**.

## Setup

```bash
cd worker
py -m venv .venv && .venv\Scripts\activate     # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
```

## Adding a company

```bash
python detect_ats.py https://careers.example.com
```

It prints a `companies.yaml` snippet once it has verified the board against the
real API. If it finds nothing, try the link of a single job posting instead: many
careers pages load their job list with JavaScript.

### Custom pages (`ats: html`)

```yaml
- name: Example Co
  ats: html
  id: exampleco                      # any unique slug
  careers_url: https://example.com/careers
  html_selectors:
    item: li.job                     # one element per job
    title: .job-title                # inside item
    link: a                          # inside item (optional)
    location: .job-location          # inside item (optional)
    render_js: false                 # true only if the page needs JavaScript (needs Playwright)
```

## Writing a new ATS adapter

1. **Look at a real response first.** Find a company that uses the ATS, call its
   public endpoint, and read the fields. Don't guess field names.
2. **Save a fixture.** Trim the real response to about 3 jobs, shorten long
   descriptions, and save it as `worker/tests/fixtures/<ats>_<company>.json`.
3. **Add the ATS** to the `ATS` enum in `internradar/models.py`.
4. **Create `internradar/adapters/<ats>.py`:**

   ```python
   class MyAtsAdapter(Adapter):
       ats = ATS.myats

       def list_jobs(self, company: Company) -> list[Job]:
           data = self.http.get_json(f"https://api.myats.example/{company.id}/jobs")
           return [self._parse(company, raw) for raw in data["jobs"]]
   ```

   - Always use `self.http`. It handles the User-Agent, rate limits and retries.
   - Build ids with `make_job_id(company.key, ats_job_id)`.
   - Turn HTML into text with `html_to_text`, and dates into datetimes with `parse_dt`.
   - If the list call has no description, set `needs_details=True` and override
     `fill_details()`. If it's paginated, override `probe()` to read only one page.
5. **Register it** in `ADAPTERS` in `internradar/adapters/__init__.py`.
6. **Add a URL pattern** to `PATTERNS` in `internradar/detect_ats.py`.
7. **Write tests** using the fixture (see `tests/test_adapters_json.py`), including
   a pagination test if the ATS pages its results.

## Commits

Keep them small, one logical change each, with a clear message.
