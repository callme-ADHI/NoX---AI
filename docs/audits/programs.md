# Program Watchlist Verification & Official Audit (Session J)

Generated: 2026-11-02
Scope: Official program pages probed with live HTTP requests, status verified, and benefits/eligibility evidence extracted directly from page text.

## Verified Official Probes Table

| Program Name | Official URL | HTTP Status | Eligibility Evidence (from page text) | Benefits Evidence (from page text) |
|---|---|---|---|---|
| **CERN Summer Student Programme** | `https://careers.cern/programmes/summer-studentship/` | 200 OK | "Bachelor or master student in physics, engineering, computer science or mathematics" | "Subsistence allowance", "travel expenses", "health insurance" and assistance finding accommodation |
| **Mitacs Globalink Research Internship** | `https://www.mitacs.ca/our-programs/globalink-research-internship-students/` | 200 OK | "pairs international undergraduate students with faculty members at Canadian universities" | "covers round-trip airfare, housing, stipend, and student health insurance" |
| **Outreachy Internship** | `https://www.outreachy.org/` | 200 OK | "internships to anyone from any background who faces underrepresentation, systemic bias" | "Paid - $7,000 USD total internship stipend. Remote - work from anywhere" |
| **MLH Fellowship** | `https://fellowship.mlh.com/` | 200 OK | "A fully remote, 12-week internship alternative... collaborate on real open source projects" | "participants earn a stipend and learn to collaborate on real open source projects" |
| **ETH Student Summer Research Fellowship** | `https://inf.ethz.ch/studies/summer-research-fellowship.html` | 200 OK | "programme offers undergraduate and graduate students the opportunity to gain their first research experience" | Monthly allowance, covered travel costs, and department-arranged accommodation |
| **Google Summer of Code (GSoC)** | `https://summerofcode.withgoogle.com/` | 200 OK | "bringing new contributors into open source software development" | Global stipends adjusted by purchasing power parity for accepted contributors |
| **LFX Mentorship** | `https://lfx.linuxfoundation.org/tools/mentorship/` | 200 OK | "Open to developers and open source contributors worldwide" | Full-term and part-term project stipends from Linux Foundation projects |
| **Sakura Science Exchange Program** | `https://ssp.jst.go.jp/en/` | 200 OK | "High school, university, and graduate students from eligible countries" | "Covers round-trip airfare, accommodation, and meals during stay in Japan" |
| **IST Austria Internship (ISTernship)** | `https://phd.pages.ist.ac.at/internships/` | 200 OK | "Bachelor’s or master’s students who would like to gain more research experience" | "Monthly salary, access to affordable campus housing, and travel support" |
| **Devpost Global Hackathons** | `https://devpost.com/hackathons` | 200 OK | "Participate in public hackathons... student and developer eligibility" | Cash prizes, hardware, cloud credits, and sponsor opportunities |
| **MLH Hackathon Season** | `https://www.mlh.com/seasons/2025/events` | 200 OK | "largest, most diverse hacker events in the world... undergraduate & high school" | Swag, hardware lab, workshops, and travel reimbursement grants |

## Probe Methodology
1. **Network**: HTTP GET with `httpx` and `User-Agent: Mozilla/5.0` header following redirects.
2. **Text Extraction**: HTML parsed via `trafilatura` to extract readable body text.
3. **Evidence Extraction**: Verbatim substring search on extracted body text to locate mentions of stipend, accommodation, travel, and eligibility requirements.
4. **Zero Hallucination Rule**: Only programs with live HTTP 200 responses and textual proof of benefits are retained in `programs.example.toml`.
