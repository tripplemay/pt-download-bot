"""测试 Download Station API 连通性（自动检测 v1/v2）"""
import asyncio
import sys
import httpx


async def test(host, username, password):
    c = httpx.AsyncClient(timeout=30, verify=False)
    api = f"{host}/webapi/entry.cgi"

    # 1. 登录
    r = await c.get(api, params={
        "api": "SYNO.API.Auth", "version": "6", "method": "login",
        "account": username, "passwd": password,
        "session": "DownloadStation", "format": "sid",
    })
    print("login:", r.json())
    sid = r.json()["data"]["sid"]

    # 2. 尝试 v2 API
    r2 = await c.get(api, params={
        "api": "SYNO.DownloadStation2.Task", "version": "2",
        "method": "list", "offset": "0", "limit": "10",
        "additional": '["detail"]', "_sid": sid,
    })
    v2_data = r2.json()
    if v2_data.get("success"):
        print("v2 API 可用")
        print("tasks:", r2.text[:1000])
        return

    # 3. 降级到 v1 API
    r1 = await c.get(api, params={
        "api": "SYNO.DownloadStation.Task", "version": "1",
        "method": "list", "_sid": sid,
    })
    v1_data = r1.json()
    if v1_data.get("success"):
        print("v1 API 可用")
        print("tasks:", r1.text[:1000])
    else:
        print("v1 也失败:", r1.text[:500])

    await c.aclose()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"
    user = sys.argv[2] if len(sys.argv) > 2 else "admin"
    pwd = sys.argv[3] if len(sys.argv) > 3 else "admin"
    asyncio.run(test(host, user, pwd))
