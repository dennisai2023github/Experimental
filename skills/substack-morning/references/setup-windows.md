# Setup on Windows (one time, about 10 minutes)

This guide is for Dennis's PC. Claude Code desktop is the primary home for this skill because it can run the fetch script and the startup reminder. Cowork is covered at the end.

## 1. Install the skill

Unzip `Substack_Morning_v1.0.skill` (it is a zip) so the folder ends up here:

```
C:\Users\amule\.claude\skills\substack-morning\
    SKILL.md
    scripts\substack_morning.py
    assets\brief_template.html
    references\...
```

Python 3.10 or newer must be installed (`python --version` in a terminal). The script uses only Python's standard library, so there is nothing to `pip install`.

## 2. Create the config and cookie file

In a terminal:

```
python C:\Users\amule\.claude\skills\substack-morning\scripts\substack_morning.py setup
```

This creates `C:\Users\amule\.substack-morning\` with:
- `.env` for your login (blank)
- `config.json` with your topics and limits (edit any time)

## 3. Add your Substack cookie (never paste it into a chat)

1. In Chrome or Edge, open https://substack.com and make sure you are signed in.
2. Press F12, open **Application**, then **Cookies**, then `https://substack.com`.
3. Find the row named `substack.sid` and copy its **Value**.
4. Open `C:\Users\amule\.substack-morning\.env` in Notepad and paste it after `SUBSTACK_SID=` on that line. Save.

The cookie gives full access to your Substack account, so treat the file like a password. The script only ever sends it to substack.com addresses, never prints it, and never writes it anywhere else. If you sign out of Substack everywhere, the cookie stops working and you repeat this step.

## 4. Test it

```
python C:\Users\amule\.claude\skills\substack-morning\scripts\substack_morning.py check
```

You should see `cookie: present`, your user id, how many publications you subscribe to, and a feed test. If it says the cookie expired, repeat step 3.

Substack has no official API. If a Substack change breaks one source, the brief still runs and shows that source in orange at the top. Tell Claude which source failed and it can adjust the script.

## 5. Make it run by itself each morning (Claude Code desktop)

The brief is built by a background Claude session that starts when you open Claude Code. That needs the `claude` command to work from a terminal. Test it:

```
claude --version
```

If Windows says it can't find `claude`, install the Claude Code command-line tool (https://code.claude.com). Or set `"claude_command"` in `C:\Users\amule\.substack-morning\config.json` to its full path.

Next, add the startup hook. Open `C:\Users\amule\.claude\settings.json` (create it if missing) and add:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"C:/Users/amule/.claude/skills/substack-morning/scripts/substack_morning.py\" status --hook"
          }
        ]
      }
    ]
  }
}
```

If the file already has content, merge the `hooks` block in rather than replacing the file. Use `py` instead of `python` if that is how Python runs on your PC.

How it behaves each day:
1. **First time you open Claude Code.** The hook starts a background Claude job and returns at once, so you can work as normal.
2. **About 2 to 5 minutes later.** The job has fetched from Substack, drafted the replies and built the page. Today's brief then **opens in your browser by itself**, with every card on it.
3. **Your next message in Claude.** Claude starts its answer with "your Substack brief is ready" and a clickable link, in case you closed the tab.
4. **Later the same day.** Opening Claude again changes nothing. It runs once per day.
5. **If something fails** (usually an expired cookie). Your next message in Claude tells you in one line what to fix, and it won't retry until tomorrow.

The background job's log for each day is in `C:\Users\amule\.substack-morning\logs\`. The hook never blocks Claude from starting, even if Python or the script has a problem.

**SYSTEM.md note.** Your SYSTEM.md has a "do not generate content until Dennis says go" gate. You chose to have this brief run automatically, so add the following line under that gate so future sessions don't fight it:

```
Exception: the substack-morning skill runs automatically at session start (approved by Dennis). Its drafts are a review queue, never published.
```

## 6. Cowork

Cowork can use the skill by uploading `Substack_Morning_v1.0.skill` under Settings, Capabilities, Skills. Two things are unverified there:
- whether Cowork's sandbox can reach substack.com, and
- whether it has a startup hook.

So the automatic morning run comes from Claude Code desktop. Open it once in the morning, even if you then work in Cowork. In Cowork itself you can type `/substack-morning` to run the brief by hand. If the fetch fails there, use Claude Code.

## 7. Optional: add it to SYSTEM.md

So every session knows about the brief, add this row to the Skill Load Guide table in `Projects\AISGRC-OS\SYSTEM.md`:

```
| Morning Substack comment queue | writing-style + platform-grammar | substack-morning |
```

## Where things are saved

- `C:\Users\amule\.substack-morning\briefs\` holds one HTML page, one Markdown copy, and one JSON file per day.
- `state.json` remembers what you have already been shown, so items never repeat.
- To start fresh, delete `state.json`. The next brief then looks back 48 hours.
