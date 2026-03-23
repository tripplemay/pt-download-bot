"""测试 Download Station API 连通性"""
import asyncio
import sys
import httpx


async def test(host, username, password):
    c = httpx.AsyncClient(timeout=30, verify=False)
    params1 = {
        "api": "SYNO.API.Auth",
        "version": "6",
        "method": "login",
        "account": username,
        "passwd": password,
        "session": "DownloadStation",
        "format": "sid",
    }
    r = await c.get(f"{host}/webapi/entry.cgi", params=params1)
    print("login:", r.json())

    sid = r.json()["data"]["sid"]
    params2 = {
        "api": "SYNO.DownloadStation.Task",
        "version": "1",
        "method": "list",
        "_sid": sid,
    }
    r2 = await c.get(f"{host}/webapi/entry.cgi", params=params2)
    print("tasks:", r2.text[:500])
    await c.aclose()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"
    user = sys.argv[2] if len(sys.argv) > 2 else "admin"
    pwd = sys.argv[3] if len(sys.argv) > 3 else "admin"
    asyncio.run(test(host, user, pwd))
