# Account Hierarchy Agent

**Link every subsidiary to the right parent account, and see where one company is split across several reps.**

> Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).

---

## The pain point

Your enrichment tool knows that Northwind Logistics GmbH belongs to Northwind Holdings. Your CRM doesn't. So the parent goes to one rep, the subsidiary to another, and nobody sees the whole account. Filling in Parent Account by hand doesn't scale, and loose name matching writes the wrong parent just often enough that nobody trusts it.

- *"We've had issues with two reps being assigned accounts from the same parent or one rep getting the parent and another getting the child for example (because our system doesn't have the relationship built.) it's not tenable to populate this manually."* ([r/salesforce: How are you populating parent:child account relationships?](https://www.reddit.com/r/salesforce/comments/1tykc5h/how_are_you_populating_parentchild_account/))
- *"Product usage spreads across subsidiaries / regional entities, but the CRM never rolls it up properly."* ([r/revops: Is messy account hierarchy blocking PLG-to-enterprise expansion?](https://www.reddit.com/r/revops/comments/1sgu10k/revops_question_is_messy_account_hierarchy/))

---

## What it does

- **Matches parents the reliable way first:** normalised domain, then the cleaned-up company name, then a fuzzy name match as a last resort
- **Only domain matches go in the import file.** Name-only and fuzzy matches go to *review*, because the wrong parent is worse than no parent
- **Spots parents that aren't in your CRM** and groups their subsidiaries, so you create each parent once
- **Checks the links you already have**, and flags duplicates hiding as parents
- **Builds the company families** and lists every family split across more than one rep, keeping the current rep on any account with open deals
- **Works with no API key:** Claude only adds a second opinion on *review* rows if you set a key

---

## Where each step runs

1. **GitHub** holds the code. Clone it or click **Code → Download ZIP**.
2. **Your CRM and enrichment tool** give you the input: one CSV of accounts with the parent name and domain from enrichment.
3. **Terminal** is where you run it: `python agent.py --input your_file.csv`.
4. **Claude is optional.** With an Anthropic key it reviews the *review* rows and writes the summary, but never changes a decision on its own.
5. **You, then your CRM.** Decide the review rows and ownership questions with sales leadership, then import `out/parent_updates_import.csv` yourself.

**The agent never writes to your CRM.**

---

## Quick start

```bash
git clone https://github.com/rubianaseem/account-hierarchy-agent.git
cd account-hierarchy-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py            # runs on the sample data, no API key needed
```

Use your own file with `python agent.py --input ~/Downloads/accounts_with_parents.csv --output out/q4`.

---

## Example output

On `sample_accounts.csv` (8 fictional accounts), with no API key:

```text
[WRITE]         A002  Northwind Logistics GmbH
                parent -> Northwind Holdings plc [A001]  via domain, confidence 0.95
[REVIEW]        A005  ACME Corp (EMEA)
                parent -> Acme Corporation [A004]  via name (exact after cleanup), confidence 0.85
[CREATE PARENT] A006  Bluebird Health Inc
                parent -> Halcyon Group

SUMMARY
8 accounts checked. 2 parent link(s) are safe to write (matched on domain), 2 need a human, 2 point to a parent that isn't in the CRM yet, and 2 need no change. 3 company families are split across more than one rep (5 account(s) to decide).
```

---

## More

- [SETUP.md](SETUP.md): Claude, running it on your real accounts, writing the links into your CRM, extending it, troubleshooting
- [docs/DETAILS.md](docs/DETAILS.md): all the pain-point quotes, full feature list and example output, cost, stack swap table, customising, how this runs today vs production, and what's in the repo
