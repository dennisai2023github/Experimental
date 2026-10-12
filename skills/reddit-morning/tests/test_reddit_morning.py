import sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
import reddit_morning as r

def post(i, age_h, sticky=False):
    return {"kind": "t3", "data": {"id": i, "title": "t" + i, "author": "a", "score": 3,
            "num_comments": 1, "permalink": f"/r/x/{i}/", "url": "u", "stickied": sticky,
            "created_utc": time.time() - age_h * 3600}}

def test_posts_filtered_by_age_and_sticky():
    L = {"data": {"children": [post("a", 1), post("b", 30), post("c", 2, True)]}}
    assert [p["id"] for p in r.parse_posts(L, time.time() - 86400)] == ["a"]

def test_comments_sorted_by_score():
    t = [{}, {"data": {"children": [
        {"kind": "t1", "data": {"author": "x", "score": 1, "body": "low"}},
        {"kind": "t1", "data": {"author": "y", "score": 9, "body": "high"}},
        {"kind": "more", "data": {}}]}}]
    assert [c["text"] for c in r.parse_comments(t, 5)] == ["high", "low"]
