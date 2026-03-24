"""查看 DS v2 任务详情（含传输进度）"""
import asyncio
import sys
import httpx


async def test(host, username, password):
    c = httpx.AsyncClient(timeout=30, verify=False)
    api = f"{host}/webapi/entry.cgi"

    r = await c.get(api, params={
        "api": "SYNO.API.Auth", "version": "6", "method": "login",
        "account": username, "passwd": password,
        "session": "DownloadStation", "format": "sid",
    })
    sid = r.json()["data"]["sid"]

    r2 = await c.get(api, params={
        "api": "SYNO.DownloadStation2.Task", "version": "2",
        "method": "list", "offset": "0", "limit": "5",
        "additional": '["detail","transfer"]',
        "_sid": sid,
    })
    import json
    data = r2.json()
    for t in data.get("data", {}).get("task", [])[:3]:
        print(json.dumps(t, indent=2, ensure_ascii=False)[:800])
        print("---")
    await c.aclose()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"
    user = sys.argv[2] if len(sys.argv) > 2 else "admin"
    pwd = sys.argv[3] if len(sys.argv) > 3 else "admin"
    asyncio.run(test(host, user, pwd))
