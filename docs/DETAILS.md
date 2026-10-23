# Details: Account Hierarchy Agent

Everything that used to sit in the main README, kept here so the README stays short. Back to the [README](../README.md).

## Background

Your enrichment tool knows that Northwind Logistics GmbH belongs to Northwind Holdings. Your CRM doesn't. So the parent gets assigned to one rep, the German subsidiary to another, two people prospect the same buying group, and nobody sees the whole account. Filling in Parent Account by hand doesn't scale, and loose name matching writes the wrong parent just often enough that nobody trusts it.

This agent reads your Salesforce account export (or HubSpot company export) plus the parent name and domain from your enrichment tool, matches each parent back to a real CRM record, and sorts every account into **safe to write**, **needs a human**, or **parent missing from the CRM**. Then it builds the company families and lists every family that's split across more than one owner, with a recommendation that protects open deals.

## Where each step runs (full version)

If you can create a repo but aren't sure where you actually *run* this, here's the whole flow in order:

1. **GitHub** is where the code lives. Copy it to your laptop with `git clone https://github.com/rubianaseem/account-hierarchy-agent.git`, or click **Code → Download ZIP** and unzip it.
2. **Your CRM and enrichment tool** are where the input comes from. Export your accounts (Salesforce report or HubSpot company export) with the parent name and parent domain columns your enrichment tool (Clay, ZoomInfo, Apollo, D&B and so on) gives you, saved as one CSV. [SETUP.md](../SETUP.md#5-run-it-on-your-real-accounts) has the steps.
3. **Terminal** (Mac Terminal, or the terminal inside Cursor) is where you run the script on that CSV. Set it up once, then run `python agent.py --input your_file.csv` (see [Quick start](../README.md#-quick-start)).
4. **Claude is optional.** With no API key the agent uses its built-in matching rules. If you add an Anthropic API key to `.env`, Claude gives a second opinion on each row marked *review* and writes the summary in plain English. It still runs in the terminal. You don't open the Claude app or paste anything into a chat, and Claude never changes the decision on its own.
5. **You, then your CRM.** Open `out/hierarchy_brief.md`, decide the *review* rows and the ownership questions with sales leadership, create any missing parents, then import `out/parent_updates_import.csv` yourself (Salesforce Data Loader, or HubSpot's parent/child company associations; steps in [SETUP.md](../SETUP.md#6-write-the-links-into-your-crm-yourself)). Owner changes are yours to make after that conversation.

**The agent never writes to your CRM.** It only reads the CSV you give it and creates files on your computer.

## Full feature list

- **Matches parents the reliable way first:** normalised domain (`https://www.Acme.com/about` → `acme.com`), then the company name with legal suffixes and region tags stripped (`ACME Corp. (EMEA)` → `acme`), then a fuzzy name match as a last resort
- **Only domain matches go in the import file.** Name-only and fuzzy matches go to *review*, because names collide and the wrong parent is worse than no parent. The threshold is one setting (`AUTO_WRITE_MIN`)
- **Spots parents that aren't in your CRM** and groups their subsidiaries, so you create the parent once and link all of them
- **Checks the links you already have:** existing Parent Account IDs that disagree with enrichment, or point to a record that no longer exists, go to *review*
- **Shows duplicates hiding as parents:** when one parent domain sits on several CRM accounts, it tells you to merge before linking
- **Builds the family tree** (walks up to the top-level parent) and prints each family with its owners and open deals
- **Ownership conflicts with a sensible default:** a child with a different owner from the parent is proposed to move to the parent's owner, unless it has open deals, in which case the current rep keeps them and the move is a conversation. New parents with subsidiaries owned by different reps get a "pick one owner" decision. Set `OWNERSHIP_RULE=flag_only` to list mismatches without proposing moves
- **Reads real exports:** Salesforce Account reports and HubSpot company exports, matched by column name (see `ALIASES`)
- **Outputs you can use:** `hierarchy_audit.csv`, `parent_updates_import.csv` (Id + ParentId), `parents_to_create.csv`, `ownership_conflicts.csv` and `hierarchy_brief.md`
- **Works with no API key.** Claude is an upgrade, not a requirement, and any API error falls back to the rules

## The pain point: all quotes and sources

This came from a [r/salesforce thread](https://www.reddit.com/r/salesforce/comments/1tykc5h/how_are_you_populating_parentchild_account/) from a team with about 30k accounts, some with hundreds of subsidiaries:

- *"We've had issues with two reps being assigned accounts from the same parent or one rep getting the parent and another getting the child for example (because our system doesn't have the relationship built.) it's not tenable to populate this manually."* ([r/salesforce: How are you populating parent:child account relationships?](https://www.reddit.com/r/salesforce/comments/1tykc5h/how_are_you_populating_parentchild_account/))
- *"I have Apollo and Clay as well but when they provide this only get account names which still have to be loose matched back to the parent."* (same post)
- *"You need a matching layer that scores domain, normalized legal name, aliases, and existing Salesforce ownership before it writes parent IDs, then a review bucket for the messy edge cases."* (a reply in the same thread)
- *"Product usage spreads across subsidiaries / regional entities, but the CRM never rolls it up properly."* and the result: *"messy parent-vs-child ownership"* ([r/revops: Is messy account hierarchy blocking PLG-to-enterprise expansion?](https://www.reddit.com/r/revops/comments/1sgu10k/revops_question_is_messy_account_hierarchy/))

That reply is basically the spec for this repo: match on domain and cleaned-up names, respect existing ownership, and put the messy cases in a review bucket instead of writing them blind. Ownership policy (who keeps a child account, how long a rep keeps an open deal) is a leadership decision, so the agent proposes and explains, and you decide.

## Quick start (full)

```bash
git clone https://github.com/rubianaseem/account-hierarchy-agent.git
cd account-hierarchy-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agent.py            # runs on the sample data, no API key needed
```

Use your own file:

```bash
python agent.py --input ~/Downloads/accounts_with_parents.csv --output out/q4
```

Optional: `cp .env.example .env` and add your `ANTHROPIC_API_KEY`. Use `--no-llm` to force the rules even when a key is set. Full walkthrough in [SETUP.md](../SETUP.md).

## Full example output (sample data, no API key)

This is the real output of `python agent.py` on `sample_accounts.csv` (8 fictional accounts with fictional owners and `.example` domains), run on 20 Oct 2026.

```text
============================================================================
 ACCOUNT HIERARCHY AGENT  |  sample_accounts.csv  |  8 accounts  |  built-in rules (no API key)
============================================================================

PARENT LINKS
----------------------------------------------------------------------------
[OK]            A001  Northwind Holdings plc
                Enrichment says this account is the top-level parent.
[WRITE]         A002  Northwind Logistics GmbH
                parent -> Northwind Holdings plc [A001]  via domain, confidence 0.95
                Parent domain northwindholdings.example matches this CRM record.
[WRITE]         A003  Northwind Retail Ltd
                parent -> Northwind Holdings plc [A001]  via domain, confidence 0.95
                Parent domain northwindholdings.example matches this CRM record.
[OK]            A004  Acme Corporation
                Top-level account (no parent in CRM or enrichment).
[REVIEW]        A005  ACME Corp (EMEA)
                parent -> Acme Corporation [A004]  via name (exact after cleanup), confidence 0.85
                'Acme Corp.' and 'Acme Corporation' are the same name once legal suffixes are stripped. No domain to confirm it.
[CREATE PARENT] A006  Bluebird Health Inc
                parent -> Halcyon Group
                'Halcyon Group' isn't in your CRM yet. Create the parent first, then link this account to it.
[CREATE PARENT] A007  Halcyon Labs
                parent -> Halcyon Group
                'Halcyon Group' isn't in your CRM yet. Create the parent first, then link this account to it.
[REVIEW]        A008  Fabrikam Ltd
                parent -> Acme Corporation [A004]  via existing link, disputed by enrichment
                CRM parent is Acme Corporation (A004), but enrichment says 'Contoso Group', which isn't in your CRM. Possibly an acquisition or an old link.

COMPANY FAMILIES (as they'd look after the proposed links)
----------------------------------------------------------------------------
+ Northwind Holdings plc [A001]  owner: Priya Shah  open opps: 1
    - Northwind Logistics GmbH [A002]  owner: Tom Becker  open opps: 0
    - Northwind Retail Ltd [A003]  owner: Priya Shah  open opps: 0
+ Acme Corporation [A004]  owner: Sam Lee  open opps: 2
    - ACME Corp (EMEA) [A005]  owner: Jordan Ellis  open opps: 1  (link needs review)
    - Fabrikam Ltd [A008]  owner: Priya Shah  open opps: 0  (link needs review)
+ Halcyon Group  (NOT IN CRM - create it)
    - Bluebird Health Inc [A006]  owner: Sam Lee  open opps: 0
    - Halcyon Labs [A007]  owner: Jordan Ellis  open opps: 1

OWNERSHIP CONFLICTS (one family, more than one rep)
----------------------------------------------------------------------------
- Northwind Logistics GmbH [A002]  Tom Becker -> Priya Shah
  No open deals. Move to the parent's owner, Priya Shah.
- ACME Corp (EMEA) [A005]  Jordan Ellis -> Sam Lee (after open deals)
  Has 1 open opportunity. Keep Jordan Ellis on those deals; agree the account move with both reps (only if you confirm the parent link).
- Fabrikam Ltd [A008]  Priya Shah -> Sam Lee
  No open deals. Move to the parent's owner, Sam Lee (only if you confirm the parent link).
- Bluebird Health Inc [A006]  Sam Lee -> decide
  2 subsidiaries of Halcyon Group are owned by Jordan Ellis and Sam Lee. Pick one owner for the new parent record.
- Halcyon Labs [A007]  Jordan Ellis -> decide
  2 subsidiaries of Halcyon Group are owned by Jordan Ellis and Sam Lee. Pick one owner for the new parent record.

============================================================================
 SUMMARY
============================================================================
8 accounts checked. 2 parent link(s) are safe to write (matched on domain), 2 need a human, 2 point to a parent that isn't in the CRM yet, and 2 need no change. 3 company families are split across more than one rep (5 account(s) to decide).
```

It also writes five files to `out/`. The import file only has the two domain matches:

```text
Id,ParentId,Account Name,Parent Account Name,Match method
A002,A001,Northwind Logistics GmbH,Northwind Holdings plc,domain
A003,A001,Northwind Retail Ltd,Northwind Holdings plc,domain
```

## 💷 Cost (estimate)

- **Without an API key:** free. It's plain Python running on your laptop.
- **With `ANTHROPIC_API_KEY`:** Claude is only called for rows marked *review*, plus one call for the summary. Domain matches, unchanged rows and missing parents never go to Claude. My rough estimate, based on [Anthropic's list pricing](https://platform.claude.com/docs/en/about-claude/pricing) for Claude Sonnet 5.5 ($2 per million input tokens, $10 per million output tokens) and about 300 input and 80 output tokens per call, is well under a US cent per call, so a few hundred review rows should cost cents rather than dollars. I haven't measured it on a large real export, so treat it as a ballpark. `--max-llm-rows` (default 50) caps how many rows go to Claude per run.
- **Your enrichment tool:** getting parent data for thousands of accounts may use credits in Clay, ZoomInfo or similar. Check that cost before you enrich, not after.

## 🔁 Stack swap table

| Layer | In this repo | Swap for |
|---|---|---|
| Account source | `sample_accounts.csv` | Salesforce Account report, HubSpot company export, a warehouse table of accounts |
| Parent data | Enriched parent name + domain columns | Clay, ZoomInfo, Apollo, D&B, Cognism, or a legal-entity dataset; anything that gives a parent name and ideally a parent domain |
| Matching | Domain → cleaned name → fuzzy name (`difflib`) | Add your own alias table, secondary domains, or an external ID (e.g. a D-U-N-S number) as the first match key |
| Second opinion | Rules → Claude | OpenAI, Grok, Gemini or a local model; swap `claude_second_opinion` |
| Output | Local CSV + Markdown | Salesforce Data Loader job, HubSpot associations API, a Slack thread per ownership conflict |
| Orchestration | You run it | n8n / Make / Zapier on a schedule, or after each enrichment refresh |

## 🛠 Customise it for your stack

Everything you'll want to tune is at the top of `agent.py` or in `.env`:

- **`ALIASES`:** your exact export headers, e.g. a custom `Ultimate Parent Domain__c` field or your Clay column names
- **`LEGAL_SUFFIXES` and `REGION_TAGS`:** the words stripped before comparing names. Add the ones your data is full of (`holdings`? `group`? only if they don't distinguish real companies for you)
- **`AUTO_WRITE_MIN`** (in `.env`): the lowest confidence that goes into the import file. Default `0.9`, so only domain matches
- **`FUZZY_MIN_RATIO`:** how similar two names must be before a fuzzy match is even suggested
- **`OWNERSHIP_RULE`** (in `.env`): `parent_wins` (default) or `flag_only`
- **Open deals rule:** in `ownership_checks`. My default keeps the current rep on any account with open deals and turns the move into a conversation. Change it if your policy says otherwise

## 🧭 How this runs today (and what production would add)

I want to be straight about what this is: a working **matching and review tool you run on a file**, not a hierarchy management platform.

**Today:**
- **No auto-trigger.** You export accounts with enrichment columns and run it by hand.
- **Prints to the console** and writes local files. **It never writes to your CRM.** Creating parents, importing links and changing owners are manual, after you've reviewed them.
- **Only as good as the parent data you give it.** If your enrichment tool only returns a parent name, most rows will land in *review*. That's on purpose.
- **One level of matching per row.** It links each account to the parent enrichment names and walks up existing links to find the top of the family. It doesn't pull a full corporate tree from a data provider.
- **Commercial vs legal parent.** Enrichment usually gives the legal parent. If your sales teams sell to a different "commercial" parent, you'll need to override those in review.
- **It doesn't decide territories, splits or compensation.** It flags where one family has several owners and suggests a default. The policy is yours.
- **No memory.** It doesn't learn from the decisions you make in review.

**What production would add:**
- A scheduled run (n8n, my default) after each enrichment refresh, reading straight from the CRM API
- Writing confirmed links back through the Salesforce API or HubSpot associations API, with a change log and an undo file
- A Slack thread per ownership conflict, tagging both reps and their manager, with the open deals listed
- Lead-to-account routing that uses the new hierarchy, so new leads at a subsidiary go to the family owner
- An alias table that learns from your review decisions, so the same match isn't asked twice
- One owner in RevOps for the hierarchy rules, agreed with sales leadership

## 📁 What's in the repo

```
account-hierarchy-agent/
├── agent.py               # the agent
├── sample_accounts.csv    # 8 fictional accounts with enrichment parent columns
├── requirements.txt       # anthropic (optional, only used with a key)
├── .env.example           # API key, model, ownership rule, auto-write threshold
├── docs/DETAILS.md        # this file: everything moved out of the README
├── SETUP.md               # setup, real exports, writing links to your CRM, extending, troubleshooting
├── LICENSE                # MIT
└── README.md
```

---

If you run account hierarchies at your company, I'd like to know where yours break most: missing parents, wrong parents, or two reps on one family.
