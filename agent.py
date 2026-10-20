#!/usr/bin/env python3
"""
Account Hierarchy Agent
=======================
Reads an account export (Salesforce Accounts or HubSpot Companies) that has
parent-company data from your enrichment tool (Clay, ZoomInfo, Apollo, D&B...),
matches each parent back to a real record in your CRM, and shows you:

  * which parent links are safe to write (matched on domain)
  * which ones need a human (name-only or fuzzy matches, CRM vs enrichment disagreements)
  * which parents don't exist in your CRM yet
  * where one company family is split across several reps

It never writes to your CRM. It creates files you review and import yourself.

Runs with no API key (built-in rules). If ANTHROPIC_API_KEY is set, Claude adds a
second opinion on the review rows and writes the brief. It still runs in the terminal.

Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).
"""
from __future__ import annotations

import argparse
import csv
import difflib
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings you'll want to tune
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-5-5"

# Your export headers -> the agent's field names. Add your own custom field names here.
ALIASES = {
    "account_id": ["account id", "id", "record id", "company id", "account_id", "18 digit account id"],
    "account_name": ["account name", "company name", "name", "account_name"],
    "domain": ["website", "company domain name", "domain", "web site"],
    "owner": ["account owner", "company owner", "owner", "account_owner"],
    "country": ["billing country", "country/region", "country", "billing country/region"],
    "parent_account_id": ["parent account id", "parentid", "parent id", "parent company id", "parent_account_id"],
    "open_opps": ["open opportunities", "open deals", "number of open deals", "open opps", "open_opportunities"],
    "parent_name": ["enriched parent name", "parent company name", "ultimate parent name",
                    "parent name", "parent company", "enrichment parent name"],
    "parent_domain": ["enriched parent domain", "parent domain", "ultimate parent domain",
                      "parent company domain", "parent website"],
}
REQUIRED = ["account_id", "account_name"]

# Legal suffixes and region tags stripped before comparing company names.
LEGAL_SUFFIXES = {
    "plc", "ltd", "limited", "inc", "incorporated", "llc", "llp", "gmbh", "ag", "sa", "sas", "bv",
    "nv", "corp", "corporation", "co", "company", "pty", "srl", "spa", "oy", "ab", "as", "kk",
}
REGION_TAGS = {"emea", "apac", "amer", "americas", "uk", "us", "usa", "eu", "europe", "dach", "nordics", "latam"}

# Match scores. Domain matches are the only ones that go straight to the import file by default.
SCORE_DOMAIN = 0.95
SCORE_NAME_EXACT = 0.85     # same name once suffixes are stripped: likely, but names collide
FUZZY_MIN_RATIO = 0.84      # difflib ratio needed to suggest a fuzzy name match at all


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load_dotenv(path: Path = Path(".env")) -> None:
    """Tiny .env reader so python-dotenv isn't needed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def norm_domain(value: str) -> str:
    """'https://www.Acme.com/about' -> 'acme.com'."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split("?")[0].split("#")[0]
    v = v.split("@")[-1]          # someone pasted an email address
    v = v.split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v.strip(".")


def norm_name(value: str) -> str:
    """'ACME Corp. (EMEA)' -> 'acme'."""
    v = (value or "").lower()
    v = re.sub(r"\(.*?\)", " ", v)             # drop bracketed tags like (EMEA)
    v = v.replace("&", " and ")
    v = re.sub(r"[^a-z0-9 ]+", " ", v)
    words = [w for w in v.split() if w not in LEGAL_SUFFIXES and w not in REGION_TAGS]
    return " ".join(words)


def to_int(value: str) -> int:
    try:
        return int(float((value or "0").strip() or 0))
    except ValueError:
        return 0


def read_accounts(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        mapping = {}
        lowered = {h.strip().lower(): h for h in headers}
        for field, names in ALIASES.items():
            for n in names:
                if n in lowered:
                    mapping[field] = lowered[n]
                    break
        missing = [f for f in REQUIRED if f not in mapping]
        if missing:
            sys.exit(f"Couldn't find column(s) for {missing}. Headers found: {headers}. "
                     f"Add your header names to ALIASES in agent.py.")
        if "parent_name" not in mapping and "parent_domain" not in mapping:
            print("  Note: no enrichment parent columns found, so the agent can only check the "
                  "Parent Account ID links you already have.")
        rows = []
        for raw in reader:
            row = {f: (raw.get(col) or "").strip() for f, col in mapping.items()}
            for f in ALIASES:
                row.setdefault(f, "")
            if not row["account_id"]:
                continue
            row["_domain"] = norm_domain(row["domain"])
            row["_name"] = norm_name(row["account_name"])
            row["_pdomain"] = norm_domain(row["parent_domain"])
            row["_pname"] = norm_name(row["parent_name"])
            row["_opps"] = to_int(row["open_opps"])
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Step 1: match each enrichment parent back to a CRM record
# ---------------------------------------------------------------------------

def build_indexes(rows):
    by_id = {r["account_id"]: r for r in rows}
    by_domain, by_name = defaultdict(list), defaultdict(list)
    for r in rows:
        if r["_domain"]:
            by_domain[r["_domain"]].append(r)
        if r["_name"]:
            by_name[r["_name"]].append(r)
    return by_id, by_domain, by_name


def match_parent(row, by_domain, by_name, all_rows):
    """Return (status, parent_row_or_None, method, confidence, note)."""
    me = row["account_id"]
    pdom, pname = row["_pdomain"], row["_pname"]
    if not pdom and not pname:
        return "no_data", None, "", 0.0, "No parent data from enrichment."

    # The account is its own parent: it's the top of its tree.
    if (pdom and pdom == row["_domain"]) or (not pdom and pname and pname == row["_name"]
                                              and not [r for r in by_name[pname] if r["account_id"] != me]):
        return "top", None, "self", 1.0, "Enrichment says this account is the top-level parent."

    if pdom:
        hits = [r for r in by_domain.get(pdom, []) if r["account_id"] != me]
        if len(hits) == 1:
            return "matched", hits[0], "domain", SCORE_DOMAIN, f"Parent domain {pdom} matches this CRM record."
        if len(hits) > 1:
            ids = ", ".join(h["account_id"] for h in hits)
            return "ambiguous", hits[0], "domain (shared)", 0.6, \
                f"Parent domain {pdom} is on {len(hits)} CRM accounts ({ids}). Probably duplicates: merge first."

    if pname:
        hits = [r for r in by_name.get(pname, []) if r["account_id"] != me]
        if len(hits) == 1:
            return "matched", hits[0], "name (exact after cleanup)", SCORE_NAME_EXACT, \
                f"'{row['parent_name']}' and '{hits[0]['account_name']}' are the same name once legal " \
                f"suffixes are stripped. No domain to confirm it."
        if len(hits) > 1:
            ids = ", ".join(h["account_id"] for h in hits)
            return "ambiguous", hits[0], "name (shared)", 0.5, f"Name matches {len(hits)} CRM accounts ({ids})."
        best, best_ratio = None, 0.0
        for r in all_rows:
            if r["account_id"] == me or not r["_name"]:
                continue
            ratio = difflib.SequenceMatcher(None, pname, r["_name"]).ratio()
            if ratio > best_ratio:
                best, best_ratio = r, ratio
        if best and best_ratio >= FUZZY_MIN_RATIO:
            return "matched", best, "name (fuzzy)", round(best_ratio * 0.8, 2), \
                f"Closest name is '{best['account_name']}' ({best_ratio:.0%} similar). Check it's really the same company."

    label = row["parent_name"] or row["parent_domain"]
    return "missing", None, "", 0.0, f"'{label}' isn't in your CRM yet."


def decide(row, status, parent, method, conf, note, by_id, auto_min):
    """Turn the match into an action: write / no_change / review / create_parent."""
    current = row["parent_account_id"]
    cur_row = by_id.get(current) if current else None
    out = {"proposed_parent_id": "", "proposed_parent_name": "", "method": method,
           "confidence": conf, "action": "", "reason": note}

    if current and not cur_row:
        out.update(action="review", reason=f"Parent Account ID {current} points to a record that isn't in "
                                            f"this export (deleted, merged or filtered out). " + note)
        if parent:
            out.update(proposed_parent_id=parent["account_id"], proposed_parent_name=parent["account_name"])
        return out

    if status == "no_data":
        if cur_row:
            out.update(action="no_change", proposed_parent_id=current, proposed_parent_name=cur_row["account_name"],
                       method="existing", reason="Keeps its existing parent. No enrichment data to check it against.")
        else:
            out.update(action="no_change", reason="Top-level account (no parent in CRM or enrichment).")
        return out

    if status == "top":
        if cur_row:
            out.update(action="review", proposed_parent_id=current, proposed_parent_name=cur_row["account_name"],
                       reason=f"CRM has a parent ({cur_row['account_name']}) but enrichment says this is a "
                              f"top-level company.")
        else:
            out.update(action="no_change", reason=note)
        return out

    if status == "missing":
        if cur_row:
            out.update(action="review", proposed_parent_id=current, proposed_parent_name=cur_row["account_name"],
                       method="existing link, disputed by enrichment",
                       reason=f"CRM parent is {cur_row['account_name']} ({current}), but enrichment says "
                              f"'{row['parent_name'] or row['parent_domain']}', which isn't in your CRM. "
                              f"Possibly an acquisition or an old link.")
        else:
            out.update(action="create_parent", proposed_parent_name=row["parent_name"] or row["parent_domain"],
                       reason=note + " Create the parent first, then link this account to it.")
        return out

    # matched or ambiguous
    out.update(proposed_parent_id=parent["account_id"], proposed_parent_name=parent["account_name"])
    if current and current == parent["account_id"]:
        out.update(action="no_change", reason="Existing parent link agrees with enrichment.")
    elif current:
        out.update(action="review", reason=f"CRM parent is {cur_row['account_name']} ({current}), enrichment "
                                           f"points to {parent['account_name']}. " + note)
    elif status == "matched" and conf >= auto_min:
        out.update(action="write")
    else:
        out.update(action="review")
    return out


# ---------------------------------------------------------------------------
# Step 2: build the families and check ownership
# ---------------------------------------------------------------------------

def ultimate_parent(acc_id, links, max_depth=10):
    seen, cur = {acc_id}, acc_id
    for _ in range(max_depth):
        nxt = links.get(cur)
        if not nxt or nxt in seen:
            return cur
        seen.add(nxt)
        cur = nxt
    return cur


def build_families(rows, decisions):
    """Group accounts into families: real CRM parents plus 'new' parents that need creating."""
    links = {}  # child -> parent id (CRM records) using written, unchanged and review links
    for r in rows:
        d = decisions[r["account_id"]]
        if d["proposed_parent_id"] and d["action"] in ("write", "no_change", "review"):
            links[r["account_id"]] = d["proposed_parent_id"]
    families = defaultdict(list)
    for r in rows:
        d = decisions[r["account_id"]]
        if d["action"] == "create_parent":
            key = "NEW:" + (r["_pdomain"] or r["_pname"])
        else:
            key = ultimate_parent(r["account_id"], links)
        families[key].append(r["account_id"])
    return families, links


def ownership_checks(families, by_id, decisions, rule):
    conflicts = []
    for key, members in families.items():
        if len(members) < 2 and not key.startswith("NEW:"):
            continue
        if key.startswith("NEW:"):
            owners = sorted({by_id[m]["owner"] for m in members if by_id[m]["owner"]})
            if len(owners) > 1:
                parent_label = decisions[members[0]]["proposed_parent_name"]
                for m in members:
                    r = by_id[m]
                    conflicts.append({
                        "family": f"{parent_label} (not in CRM yet)", "account_id": m,
                        "account_name": r["account_name"], "current_owner": r["owner"],
                        "proposed_owner": "decide", "open_opps": r["_opps"],
                        "recommendation": f"{len(members)} subsidiaries of {parent_label} are owned by "
                                          f"{' and '.join(owners)}. Pick one owner for the new parent record.",
                    })
            continue
        top = by_id.get(key)
        if not top:
            continue
        for m in members:
            if m == key:
                continue
            r = by_id[m]
            if not r["owner"] or r["owner"] == top["owner"]:
                continue
            link_state = decisions[m]["action"]
            caveat = " (only if you confirm the parent link)" if link_state == "review" else ""
            if rule == "flag_only":
                rec, proposed = "Different owner from the parent. Flagged only (OWNERSHIP_RULE=flag_only).", r["owner"]
            elif r["_opps"] > 0:
                rec = (f"Has {r['_opps']} open opportunit{'y' if r['_opps'] == 1 else 'ies'}. Keep {r['owner']} "
                       f"on those deals; agree the account move with both reps{caveat}.")
                proposed = f"{top['owner']} (after open deals)"
            else:
                rec = f"No open deals. Move to the parent's owner, {top['owner']}{caveat}."
                proposed = top["owner"]
            conflicts.append({
                "family": top["account_name"], "account_id": m, "account_name": r["account_name"],
                "current_owner": r["owner"], "proposed_owner": proposed, "open_opps": r["_opps"],
                "recommendation": rec,
            })
    return conflicts


# ---------------------------------------------------------------------------
# Optional: Claude
# ---------------------------------------------------------------------------

def get_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # noqa: WPS433
        return anthropic.Anthropic()
    except Exception as exc:  # pragma: no cover
        print(f"  (anthropic not available: {exc}. Using built-in rules.)")
        return None


def ask_claude(client, model, prompt, max_tokens=300):
    resp = client.messages.create(model=model, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": prompt}])
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


def claude_second_opinion(client, model, row, decision):
    prompt = (
        "You are helping a RevOps team build parent/child account hierarchies in their CRM.\n"
        "Give a second opinion on ONE proposed link. Use only the facts below. Do not invent facts "
        "about these companies. Answer in at most two sentences, starting with one of: "
        "LIKELY SAME COMPANY, LIKELY DIFFERENT, NOT ENOUGH INFO.\n\n"
        f"Child account: {row['account_name']} | website {row['domain'] or '-'} | country {row['country'] or '-'}\n"
        f"Enrichment says parent is: {row['parent_name'] or '-'} | domain {row['parent_domain'] or '-'}\n"
        f"Proposed CRM parent: {decision['proposed_parent_name'] or '-'} (id {decision['proposed_parent_id'] or '-'})\n"
        f"Why it needs review: {decision['reason']}\n"
    )
    return ask_claude(client, model, prompt, max_tokens=150)


def claude_summary(client, model, base_summary, conflicts):
    prompt = (
        "Rewrite this account-hierarchy audit summary for a sales leader in 3-4 plain sentences. "
        "Keep every number exactly as given, invent nothing, no bullet points.\n\n"
        f"{base_summary}\n\nOwnership conflicts:\n" +
        "\n".join(f"- {c['account_name']}: {c['recommendation']}" for c in conflicts)
    )
    return ask_claude(client, model, prompt, max_tokens=250)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

ICON = {"write": "[WRITE]", "no_change": "[OK]", "review": "[REVIEW]", "create_parent": "[CREATE PARENT]"}


def print_tree(families, by_id, decisions):
    print("\nCOMPANY FAMILIES (as they'd look after the proposed links)")
    print("-" * 76)
    order = sorted(families.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for key, members in order:
        if key.startswith("NEW:"):
            label = decisions[members[0]]["proposed_parent_name"]
            print(f"+ {label}  (NOT IN CRM - create it)")
            for m in members:
                r = by_id[m]
                print(f"    - {r['account_name']} [{m}]  owner: {r['owner'] or '-'}  open opps: {r['_opps']}")
            continue
        top = by_id[key]
        if len(members) == 1:
            continue
        print(f"+ {top['account_name']} [{key}]  owner: {top['owner'] or '-'}  open opps: {top['_opps']}")
        for m in members:
            if m == key:
                continue
            r, d = by_id[m], decisions[m]
            tag = "" if d["action"] in ("write", "no_change") else "  (link needs review)"
            print(f"    - {r['account_name']} [{m}]  owner: {r['owner'] or '-'}  open opps: {r['_opps']}{tag}")
    singles = [k for k, v in families.items() if not k.startswith("NEW:") and len(v) == 1]
    if singles:
        names = ", ".join(by_id[k]["account_name"] for k in singles)
        print(f"  Standalone: {names}")


def write_outputs(outdir, rows, decisions, conflicts, brief):
    outdir.mkdir(parents=True, exist_ok=True)
    with (outdir / "hierarchy_audit.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["account_id", "account_name", "owner", "current_parent_id", "enrichment_parent",
                    "proposed_parent_id", "proposed_parent_name", "match_method", "confidence",
                    "action", "reason", "claude_note"])
        for r in rows:
            d = decisions[r["account_id"]]
            w.writerow([r["account_id"], r["account_name"], r["owner"], r["parent_account_id"],
                        " / ".join(x for x in (r["parent_name"], r["parent_domain"]) if x),
                        d["proposed_parent_id"], d["proposed_parent_name"], d["method"], d["confidence"],
                        d["action"], d["reason"], d.get("claude_note", "")])
    with (outdir / "parent_updates_import.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Id", "ParentId", "Account Name", "Parent Account Name", "Match method"])
        for r in rows:
            d = decisions[r["account_id"]]
            if d["action"] == "write":
                w.writerow([r["account_id"], d["proposed_parent_id"], r["account_name"],
                            d["proposed_parent_name"], d["method"]])
    with (outdir / "parents_to_create.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Parent name", "Parent domain", "Child account IDs", "Child owners"])
        grouped = defaultdict(list)
        for r in rows:
            if decisions[r["account_id"]]["action"] == "create_parent":
                grouped[(r["parent_name"], r["_pdomain"])].append(r)
        for (pname, pdom), kids in grouped.items():
            w.writerow([pname, pdom, " ".join(k["account_id"] for k in kids),
                        ", ".join(sorted({k["owner"] for k in kids if k["owner"]}))])
    with (outdir / "ownership_conflicts.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = ["family", "account_id", "account_name", "current_owner", "proposed_owner",
                  "open_opps", "recommendation"]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(conflicts)
    (outdir / "hierarchy_brief.md").write_text(brief, encoding="utf-8")


def make_brief(src, mode, counts, summary, rows, decisions, conflicts):
    lines = [f"# Account hierarchy brief: {src}", "",
             f"Run on {date.today():%d %b %Y} with {mode}.", "", "## Summary", "", summary, ""]
    lines += ["## Safe to write (matched on domain)", ""]
    w = [r for r in rows if decisions[r["account_id"]]["action"] == "write"]
    lines += [f"- {r['account_name']} -> {decisions[r['account_id']]['proposed_parent_name']}" for r in w] or ["- none"]
    lines += ["", "## Needs a human", ""]
    rv = [r for r in rows if decisions[r["account_id"]]["action"] == "review"]
    for r in rv:
        d = decisions[r["account_id"]]
        lines.append(f"- **{r['account_name']}**: {d['reason']}")
        if d.get("claude_note"):
            lines.append(f"  - Claude: {d['claude_note']}")
    if not rv:
        lines.append("- none")
    lines += ["", "## Parents to create", ""]
    cp = [r for r in rows if decisions[r["account_id"]]["action"] == "create_parent"]
    lines += [f"- {decisions[r['account_id']]['proposed_parent_name']} (child: {r['account_name']}, owner {r['owner']})"
              for r in cp] or ["- none"]
    lines += ["", "## Ownership decisions", ""]
    lines += [f"- {c['account_name']} ({c['current_owner']}): {c['recommendation']}" for c in conflicts] or ["- none"]
    lines += ["", "Nothing has been written to your CRM. Review `hierarchy_audit.csv`, then import "
              "`parent_updates_import.csv` yourself.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Match enrichment parents to CRM accounts and flag split ownership.")
    ap.add_argument("--input", default="sample_accounts.csv", help="Account/company export CSV")
    ap.add_argument("--output", default="out", help="Folder for the output files")
    ap.add_argument("--no-llm", action="store_true", help="Use built-in rules even if an API key is set")
    ap.add_argument("--max-llm-rows", type=int, default=50, help="Cap on review rows sent to Claude")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"File not found: {src}")
    auto_min = float(os.environ.get("AUTO_WRITE_MIN", "0.9") or 0.9)
    rule = (os.environ.get("OWNERSHIP_RULE", "parent_wins") or "parent_wins").strip().lower()

    rows = read_accounts(src)
    if not rows:
        sys.exit("No account rows found.")
    by_id, by_domain, by_name = build_indexes(rows)

    client = None if args.no_llm else get_client()
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
    mode = f"Claude ({model})" if client else "built-in rules (no API key)"

    print("=" * 76)
    print(f" ACCOUNT HIERARCHY AGENT  |  {src.name}  |  {len(rows)} accounts  |  {mode}")
    print("=" * 76)

    decisions = {}
    for r in rows:
        status, parent, method, conf, note = match_parent(r, by_domain, by_name, rows)
        decisions[r["account_id"]] = decide(r, status, parent, method, conf, note, by_id, auto_min)

    llm_calls, failures = 0, 0
    for r in rows:
        d = decisions[r["account_id"]]
        if not client or d["action"] != "review" or llm_calls >= args.max_llm_rows or failures >= 2:
            continue
        try:
            d["claude_note"] = claude_second_opinion(client, model, r, d)
            llm_calls += 1
            failures = 0
        except Exception as exc:
            failures += 1
            print(f"  Claude call failed ({type(exc).__name__}). Keeping the rules' answer.")

    print("\nPARENT LINKS")
    print("-" * 76)
    for r in rows:
        d = decisions[r["account_id"]]
        print(f"{ICON[d['action']]:<16}{r['account_id']}  {r['account_name']}")
        if d["proposed_parent_name"] and d["action"] != "no_change":
            pid = f" [{d['proposed_parent_id']}]" if d["proposed_parent_id"] else ""
            conf = f"  via {d['method']}" if d["method"] else ""
            conf += f", confidence {d['confidence']:.2f}" if d["confidence"] else ""
            print(f"{'':16}parent -> {d['proposed_parent_name']}{pid}{conf}")
        print(f"{'':16}{d['reason']}")
        if d.get("claude_note"):
            print(f"{'':16}claude: {d['claude_note']}")

    families, _ = build_families(rows, decisions)
    print_tree(families, by_id, decisions)

    conflicts = ownership_checks(families, by_id, decisions, rule)
    print("\nOWNERSHIP CONFLICTS (one family, more than one rep)")
    print("-" * 76)
    if not conflicts:
        print("None found.")
    for c in conflicts:
        print(f"- {c['account_name']} [{c['account_id']}]  {c['current_owner']} -> {c['proposed_owner']}")
        print(f"  {c['recommendation']}")

    counts = defaultdict(int)
    for d in decisions.values():
        counts[d["action"]] += 1
    split_families = len({c["family"] for c in conflicts})
    summary = (f"{len(rows)} accounts checked. {counts['write']} parent link(s) are safe to write "
               f"(matched on domain), {counts['review']} need a human, {counts['create_parent']} point to a "
               f"parent that isn't in the CRM yet, and {counts['no_change']} need no change. "
               f"{split_families} company famil{'y is' if split_families == 1 else 'ies are'} split across "
               f"more than one rep ({len(conflicts)} account(s) to decide).")
    if client:
        try:
            summary = claude_summary(client, model, summary, conflicts) or summary
        except Exception as exc:
            print(f"  Claude summary failed ({type(exc).__name__}). Using the template.")

    print("\n" + "=" * 76)
    print(" SUMMARY")
    print("=" * 76)
    print(summary)

    outdir = Path(args.output)
    brief = make_brief(src.name, mode, counts, summary, rows, decisions, conflicts)
    write_outputs(outdir, rows, decisions, conflicts, brief)
    print(f"\nFiles written to {outdir}/: hierarchy_audit.csv, parent_updates_import.csv, "
          f"parents_to_create.csv, ownership_conflicts.csv, hierarchy_brief.md")
    print("Nothing was written to your CRM.")


if __name__ == "__main__":
    main()
