#!/usr/bin/env python3
"""Fetch the last day of r/AI_Governance (read-only, public JSON, no login).

Usage: python reddit_morning.py [--sub AI_Governance] [--hours 24] [--comments 5] [--out FILE]
Prints JSON: {"subreddit", "since", "posts": [...]} with top comments per post.
"""
import argparse, json, sys, time, urllib.request

UA = "reddit-morning/1.0 (personal daily digest; read-only)"


def get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(2 ** (i + 1))


def parse_posts(listing, cutoff):
    out = []
    for c in listing["data"]["children"]:
        d = c["data"]
        if d.get("created_utc", 0) < cutoff or d.get("stickied"):
            continue
        out.append({
            "id": d["id"], "title": d["title"], "author": d.get("author"),
            "score": d.get("score", 0), "num_comments": d.get("num_comments", 0),
            "flair": d.get("link_flair_text"), "url": d.get("url"),
            "permalink": "https://www.reddit.com" + d["permalink"],
            "text": (d.get("selftext") or "")[:2000],
            "created_utc": d["created_utc"],
        })
    return out


def parse_comments(thread, n):
    kids = thread[1]["data"]["children"]
    cs = [k["data"] for k in kids if k["kind"] == "t1"]
    cs.sort(key=lambda d: d.get("score", 0), reverse=True)
    return [{"author": d.get("author"), "score": d.get("score", 0),
             "text": (d.get("body") or "")[:600]} for d in cs[:n]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", default="AI_Governance")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--comments", type=int, default=5)
    ap.add_argument("--out")
    a = ap.parse_args()
    cutoff = time.time() - a.hours * 3600
    base = f"https://www.reddit.com/r/{a.sub}"
    posts = parse_posts(get(f"{base}/new.json?limit=50&raw_json=1"), cutoff)
    for p in posts:
        if p["num_comments"]:
            time.sleep(1)
            try:
                p["top_comments"] = parse_comments(
                    get(f"https://www.reddit.com/comments/{p['id']}.json?limit=50&raw_json=1"), a.comments)
            except Exception as e:
                p["top_comments"] = []
                p["comments_error"] = str(e)
    res = {"subreddit": a.sub, "since": cutoff, "posts": posts}
    s = json.dumps(res, indent=2)
    if a.out:
        open(a.out, "w").write(s)
    print(s)


if __name__ == "__main__":
    sys.exit(main())
