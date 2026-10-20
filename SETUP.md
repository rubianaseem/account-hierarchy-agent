# Setup guide

This walks you from zero to a run on your own accounts. You need Python 3.10 or newer and a terminal (Mac Terminal, or the terminal inside Cursor).

Check your Python version with `python3 --version`. If it's older than 3.10, install a newer one from [python.org](https://www.python.org/downloads/) or with `brew install python`.

---

## 1. Get the code and create a virtual environment

```bash
git clone https://github.com/rubianaseem/account-hierarchy-agent.git
cd account-hierarchy-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The virtual environment keeps this project's packages separate from everything else on your laptop. Run `source .venv/bin/activate` again each time you open a new terminal.

## 2. Run it on the sample data

```bash
python agent.py
```

No API key needed. You should see 8 accounts sorted into `[OK]`, `[WRITE]`, `[REVIEW]` and `[CREATE PARENT]`, three company families, five ownership conflicts and a summary that says *2 parent link(s) are safe to write*. The files land in `out/`:

| File | What it's for |
|---|---|
| `hierarchy_audit.csv` | Every account, the proposed parent, how it was matched, the confidence, the action and why |
| `parent_updates_import.csv` | Only the links safe to write (`Id`, `ParentId`), ready for Salesforce Data Loader |
| `parents_to_create.csv` | Parent companies that aren't in your CRM yet, with their subsidiaries and current owners |
| `ownership_conflicts.csv` | Accounts in a family owned by someone other than the parent's owner, with a recommendation |
| `hierarchy_brief.md` | A short write-up to share with sales leadership |

## 3. Understand the actions

- **WRITE:** the parent domain from enrichment matches exactly one CRM account. Safe to import.
- **OK:** nothing to change (top-level account, or the existing link agrees with enrichment).
- **REVIEW:** a human decides. Name-only or fuzzy matches, a parent domain shared by several CRM accounts (likely duplicates), or an existing Parent Account ID that disagrees with enrichment or points to a record that's gone.
- **CREATE PARENT:** enrichment names a parent that isn't in your CRM. Create it, then link the children.

## 4. Optional: add Claude

```bash
cp .env.example .env
```

Open `.env` and paste your key after `ANTHROPIC_API_KEY=` (get one at https://console.anthropic.com). The default model is `claude-sonnet-5-5`; change `ANTHROPIC_MODEL` if you want another one.

Run `python agent.py` again. The header now says `Claude (claude-sonnet-5-5)`. Claude only sees the rows marked *review*. It answers with *LIKELY SAME COMPANY*, *LIKELY DIFFERENT* or *NOT ENOUGH INFO* and a short reason, shown under `claude:` in the terminal and in the `claude_note` column. **The row stays in review either way.** Claude also rewrites the summary for a sales audience. If a call fails (bad key, no credit, network), the rules' answer stays; after two failures in a row the rest of the run skips Claude. Use `--no-llm` to skip it even when a key is set.

`.env` is in `.gitignore`, so your key is never committed.

Other settings in `.env`:

- `OWNERSHIP_RULE`: `parent_wins` (default) proposes moving a child to the parent's owner; `flag_only` lists mismatches without proposing moves.
- `AUTO_WRITE_MIN`: the lowest confidence that goes into the import file. Default `0.9` (domain matches only). Lowering it to `0.85` would also write exact-name matches. I wouldn't.

## 5. Run it on your real accounts

The agent needs an **account ID** and **account name**. For matching it uses the **website/domain**, plus the **parent name and parent domain** from your enrichment tool. Owner, country, existing parent ID and open deal count make the output more useful. Column names are matched automatically (see `ALIASES` in `agent.py`).

| Agent field | Salesforce Account report | HubSpot company export | Enrichment tool |
|---|---|---|---|
| `account_id` | Account ID | Record ID | (keep the CRM ID in the sheet) |
| `account_name` | Account Name | Company name | |
| `domain` | Website | Company Domain Name | |
| `owner` | Account Owner | Company owner | |
| `country` | Billing Country | Country/Region | |
| `parent_account_id` | Parent Account ID | (leave out, or a custom property) | |
| `open_opps` | (a roll-up or count you add) | Number of open deals, if you have it | |
| `parent_name` | | | Parent / ultimate parent company name |
| `parent_domain` | | | Parent / ultimate parent domain |

### Salesforce

1. **Reports → New Report → Accounts.**
2. Add the columns Account ID, Account Name, Website, Account Owner, Billing Country and Parent Account ID. Filter to the accounts you care about (for example, one segment or region first).
3. Click the arrow next to **Edit → Export → Details Only**, format **Comma Delimited .csv**.
4. If you want the open-deal protection, add a column with the number of open opportunities per account (a roll-up summary field, or counts from an Opportunities report pasted in by Account ID).

### HubSpot

1. Go to **CRM → Companies** and switch to the table view.
2. Click **Edit columns** and add Record ID, Company name, Company Domain Name, Company owner and Country/Region (and an open-deals count if you have one).
3. Click **Export**, choose **CSV**, and include only the columns in the view. HubSpot emails you a download link when the file is ready.

### Add the parent columns from your enrichment tool

1. Load the export into your enrichment tool (a Clay table, a ZoomInfo or Apollo enrichment, a D&B file), keeping the CRM ID column untouched.
2. Add whatever your tool gives for the parent or ultimate parent: the **name**, and the **domain** if it has one. Domain is what makes a match safe to write; with names only, most rows will come back as *review*.
3. Export as CSV (UTF-8) and run:

```bash
python agent.py --input ~/Downloads/accounts_with_parents.csv --output out/review-1
```

Enrichment can use paid credits. Check the cost for your row count before you run it, and start with one segment.

If your column names differ (custom fields, other languages), add them to `ALIASES` at the top of `agent.py`.

## 6. Write the links into your CRM yourself

The agent never does this for you. Do the review first, then:

1. **Agree the ownership questions** in `ownership_conflicts.csv` with sales leadership, especially rows with open deals.
2. **Create the missing parents** from `parents_to_create.csv` (or merge duplicates the review flagged), and note their new IDs.
3. **Add confirmed review rows** to `parent_updates_import.csv` with the right parent ID.

### Salesforce

Use **Data Loader** (or Workbench): choose **Update** on the **Account** object, select `parent_updates_import.csv`, map `Id` → Account ID and `ParentId` → Parent Account ID, and run it. Export the same accounts first so you have a backup of the old values. Then open a parent account and use **View Account Hierarchy** to check the result.

### HubSpot

HubSpot stores this as a parent/child **association** between companies, not a field. For a handful, open the child company and add the parent in its associations card. For bulk updates, check HubSpot's current knowledge base for importing company-to-company associations, or use the associations API. If you'd rather not touch associations yet, create a custom company property such as `Parent CRM ID` and import `ParentId` into it, matched on Record ID, so you can report on families straight away.

## 7. Extend it with an AI coding tool

The code is one file with clear sections, so it's easy to extend with an AI coding assistant. Open the folder in your tool and try prompts like these:

- **Cursor:** open the folder, press Cmd+L and ask: *"Add a second input file of known aliases (alias name → account ID) and check it before the fuzzy name match."*
- **Claude Code:** run `claude` in the project folder and ask: *"Read accounts straight from Salesforce with simple-salesforce using credentials from .env instead of a CSV, keeping the CSV option."*
- **Codex CLI:** run `codex` in the folder and ask: *"Post each ownership conflict to a Slack webhook from .env, mentioning both owners."*
- **Grok:** paste `agent.py` and ask it to add secondary domains (a semicolon-separated column) to the domain index.

Ask the tool to run `python agent.py` afterwards and check that the sample still gives 2 WRITE, 2 REVIEW, 2 CREATE PARENT and 2 OK.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `Couldn't find column(s) for ['account_id']` | Your headers have names the agent doesn't know. Add them to `ALIASES` in `agent.py`, or rename the column to `Account ID`. |
| `no enrichment parent columns found` | The file has no parent name/domain columns, so only existing Parent Account IDs are checked. Add the enrichment columns (step 5). |
| Almost everything is REVIEW | Your enrichment only gives parent names. Add parent domains, or add an alias list (see step 7). |
| A parent shows as CREATE PARENT but it is in the CRM | Its website in the CRM is blank or different from the enrichment domain, and the names don't match closely. Fill in the website, or add the name variant to your aliases. |
| Two different companies got a fuzzy match | Raise `FUZZY_MIN_RATIO` at the top of `agent.py`. Fuzzy matches are always review, never written. |
| `Claude call failed (AuthenticationError)` | The key in `.env` is wrong or has no credit. The run still finishes using the rules. |
| `Claude call failed (NotFoundError)` | The model name in `ANTHROPIC_MODEL` isn't available on your account. Pick one from Anthropic's models page. |
| `ModuleNotFoundError: anthropic` | Activate the venv (`source .venv/bin/activate`) and run `pip install -r requirements.txt`. Without a key the agent doesn't need it at all. |
| Weird characters in names | Export as UTF-8 CSV. The agent already handles the Excel byte-order mark. |
