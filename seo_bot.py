#!/usr/bin/env python3
"""SEO Content Job Bot — public-page analysis, no platform automation.

Python 3.10+, standard library only. Outputs HTML/Markdown/JSON and draft metadata.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

MAX_BYTES = 1_500_000
USER_AGENT = "SEOContentHelper/1.0 (single page SEO analysis; no automated engagement)"

class RedirectError(Exception):
    pass


def clean_text(value, limit=350):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Public HTTP or HTTPS link එකක් පමණක් භාවිත කරන්න.")
    if parsed.port and parsed.port not in (80, 443):
        raise ValueError("Only standard web ports (80/443) supported.")
    name = parsed.hostname
    if name.lower() in {"localhost", "localhost.localdomain"} or name.lower().endswith((".local", ".internal")):
        raise ValueError("Local / internal domains are blocked.")
    try:
        infos = socket.getaddrinfo(name, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Domain එක resolve කළ නොහැක.") from exc
    if not infos or any(not ipaddress.ip_address(item[4][0]).is_global for item in infos):
        raise ValueError("Only public internet hosts are accepted.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self):
        self.count = 0
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > 3:
            raise RedirectError("Too many redirects")
        allowed = validate_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, allowed)


class Analyzer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.robots = ""
        self.h1 = []
        self.h2 = []
        self.images = 0
        self.images_without_alt = 0
        self.links = []
        self.in_title = False
        self.in_heading = None
        self.in_noscript = False
        self.in_script = False
        self.in_style = False
        self.text_chunks = []
        self.heading_buffer = []
        self.title_chunks = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title": self.in_title = True
        elif tag in ("h1", "h2"):
            self.in_heading = tag
            self.heading_buffer = []
        elif tag == "script": self.in_script = True
        elif tag == "style": self.in_style = True
        elif tag == "noscript": self.in_noscript = True
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            if name == "description" and not self.description:
                self.description = clean_text(a.get("content", ""), 1000)
            elif name == "robots" and not self.robots:
                self.robots = clean_text(a.get("content", ""), 300)
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonical = a.get("href", "")[:500]
        elif tag == "img":
            self.images += 1
            if not clean_text(a.get("alt", "")):
                self.images_without_alt += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"][:500])
    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            self.title = clean_text(" ".join(self.title_chunks), 1000)
        elif tag == self.in_heading:
            val = clean_text(" ".join(self.heading_buffer), 400)
            if val:
                (self.h1 if tag == "h1" else self.h2).append(val)
            self.in_heading = None
            self.heading_buffer = []
        elif tag == "script": self.in_script = False
        elif tag == "style": self.in_style = False
        elif tag == "noscript": self.in_noscript = False
    def handle_data(self, data):
        if self.in_title: self.title_chunks.append(data)
        if self.in_heading: self.heading_buffer.append(data)
        if not (self.in_script or self.in_style or self.in_noscript):
            self.text_chunks.append(data)


def fetch_and_analyze(url):
    checked = validate_url(url)
    req = Request(checked, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    opener = build_opener(SafeRedirect())
    with opener.open(req, timeout=15) as resp:
        final_url = validate_url(resp.geturl())
        ctype = resp.headers.get("content-type", "").lower()
        if "html" not in ctype:
            raise ValueError("Target is not an HTML page. Content-Type: " + ctype[:100])
        data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Page exceeds the 1.5 MB inspection limit.")
        # The first page may not declare encoding properly. Fall back safely.
        charset = resp.headers.get_content_charset() or "utf-8"
    parser = Analyzer()
    parser.feed(data.decode(charset, errors="replace"))
    text = clean_text(" ".join(parser.text_chunks), 100000)
    word_count = len(text.split())
    return final_url, parser, word_count


def generate_audit(url, keyword, job_title, notes, parser, words):
    site = urlsplit(url).hostname or "website"
    keyword = clean_text(keyword, 100)
    existing_title = parser.title
    existing_description = parser.description
    title_suggestion = clean_text(f"{keyword.title()} | {site}", 60) if keyword else clean_text(existing_title or site, 60)
    description_suggestion = clean_text(
        f"Explore {keyword} with helpful information and clear next steps from {site}. Learn more on the website."
        if keyword else f"Explore helpful information, resources, and updates at {site}.", 155)
    findings = []
    findings.append(("Title", "OK" if 30 <= len(existing_title) <= 60 else "Review", f"{len(existing_title)} characters; target approx. 30–60"))
    findings.append(("Meta description", "OK" if 70 <= len(existing_description) <= 160 else "Review", f"{len(existing_description)} characters; target approx. 70–160"))
    findings.append(("H1 headings", "OK" if len(parser.h1) == 1 else "Review", f"Found {len(parser.h1)}"))
    findings.append(("Image alt text", "OK" if not parser.images_without_alt else "Review", f"{parser.images_without_alt} of {parser.images} images missing or empty alt text"))
    findings.append(("Canonical URL", "OK" if parser.canonical else "Review", "Present" if parser.canonical else "Missing"))
    findings.append(("Visible words (estimate)", "Info", str(words)))
    if parser.robots:
        findings.append(("Meta robots", "Info", parser.robots))
    hostname = (urlsplit(url).hostname or "").lower()
    external_links = sum(1 for link in parser.links if urlsplit(urljoin(url, link)).hostname and (urlsplit(urljoin(url, link)).hostname or "").lower() != hostname)
    internal_links = len(parser.links) - external_links
    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": "SEO + Promote Content + Search + Engage (legitimate content tasks only)",
        "job_title": clean_text(job_title, 250),
        "job_notes": clean_text(notes, 900),
        "public_url": url,
        "keyword": keyword,
        "on_page": {
            "title": existing_title,
            "meta_description": existing_description,
            "h1": parser.h1[:8],
            "h2": parser.h2[:12],
            "canonical": parser.canonical,
            "robots": parser.robots,
            "images_total": parser.images,
            "images_missing_alt": parser.images_without_alt,
            "links_internal_estimate": internal_links,
            "links_external_estimate": external_links,
            "visible_word_count_estimate": words,
        },
        "checks": [{"check": a, "result": b, "detail": c} for a,b,c in findings],
        "drafts_review_before_use": {
            "seo_title": title_suggestion,
            "meta_description": description_suggestion,
            "social_caption": f"Explore {keyword} on {site}. Read the details and decide what is useful to you. {url}" if keyword else f"Explore resources from {site}: {url}",
            "plain_proof": f"SEO inspection performed on {url} at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Found {len(parser.h1)} H1 heading(s), {parser.images} images, {parser.images_without_alt} lacking alt text. This report documents an SEO inspection ONLY; it does not verify any sharing, engagement, clicks, rankings, or publication."
        },
        "limitations": [
            "Public HTML page only; no account login, no SproutGigs submissions.",
            "JavaScript-rendered content may be missing.",
            "Metrics are heuristics, not SEO ranking guarantees.",
            "Drafts require user review; do not claim content was published or promoted.",
        ]
    }


def write_reports(report, dest):
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "seo_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Genuine SEO page inspection", "", f"**Page:** {report['public_url']}", f"**Checked (UTC):** {report['checked_at_utc']}", f"**Target keyword:** {report['keyword']}", "", "## Findings", "", "| Check | Result | Detail |", "|---|---|---|"]
    for f in report["checks"]:
        clean_cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {clean_cell(f['check'])} | {clean_cell(f['result'])} | {clean_cell(f['detail'])} |")
    lines += ["", "## Observed page metadata", "", f"**Title:** {report['on_page']['title']}", "", f"**Description:** {report['on_page']['meta_description']}", "", f"**H1:** {', '.join(report['on_page']['h1']) or '(none)'}", "", "## Draft content (review before use)", ""]
    for k,v in report["drafts_review_before_use"].items():
        lines.append(f"**{k.replace('_', ' ').title()}**: {v}")
        lines.append("")
    lines += ["## Limitations", ""] + ["- " + x for x in report["limitations"]]
    (dest / "SEO_Report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    rows = "".join(f"<tr><td>{html.escape(f['check'])}</td><td>{html.escape(f['result'])}</td><td>{html.escape(f['detail'])}</td></tr>" for f in report["checks"])
    drafts = "".join(f"<section><h3>{html.escape(k.replace('_', ' ').title())}</h3><p>{html.escape(v)}</p></section>" for k,v in report["drafts_review_before_use"].items())
    webpage = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SEO Bot — Inspection Report</title><style>body{{font-family:system-ui,sans-serif;line-height:1.6;background:#f6f8fb;color:#1b2434;margin:0;padding:20px}}main{{max-width:760px;margin:auto}}header,section{{background:white;padding:18px;border-radius:14px;margin:12px 0;border:1px solid #e1e7f0;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%}}td,th{{padding:9px;border-bottom:1px solid #eee;text-align:left}}h1{{font-size:1.6rem}}p{{white-space:pre-wrap}}</style><main><header><h1>SEO Page Inspection</h1><p>Public page: {html.escape(report['public_url'])}<br>Checked: {html.escape(report['checked_at_utc'])}</p><p>This is an actual SEO inspection, <strong>not proof of clicks, shares or engagement.</strong></p></header><section><h2>Checks</h2><table><tr><th>Item</th><th>Status</th><th>Detail</th></tr>{rows}</table></section><section><h2>Observed page metadata</h2><p><b>Title:</b> {html.escape(report['on_page']['title'])}</p><p><b>Meta description:</b> {html.escape(report['on_page']['meta_description'])}</p></section><section><h2>Drafts</h2></section>{drafts}<section><h2>Limitations</h2><ul>{''.join('<li>'+html.escape(z)+'</li>' for z in report['limitations'])}</ul></section></main></html>'''
    (dest / "SEO_Report.html").write_text(webpage, encoding="utf-8")
    print("Created files in", dest)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--url", default=os.getenv("BOT_URL", ""))
    cli.add_argument("--keyword", default=os.getenv("BOT_KEYWORD", ""))
    cli.add_argument("--job-title", default=os.getenv("BOT_JOB_TITLE", ""))
    cli.add_argument("--notes", default=os.getenv("BOT_NOTES", ""))
    cli.add_argument("--demo-html", default="", help="Local HTML fixture for a self-contained demo")
    cli.add_argument("--output", default="reports")
    args = cli.parse_args()
    if args.demo_html:
        content = Path(args.demo_html).read_text(encoding="utf-8")
        page = Analyzer()
        page.feed(content)
        words = len(clean_text(" ".join(page.text_chunks), 100000).split())
        url = "https://example.com/demo"
    else:
        if not args.url:
            cli.error("Please supply a public URL.")
        url, page, words = fetch_and_analyze(args.url)
    report = generate_audit(url, args.keyword, args.job_title, args.notes, page, words)
    write_reports(report, Path(args.output))


if __name__ == "__main__":
    main()
