"""测试 Download Station v2 API 创建任务的参数格式"""
import asyncio
import json
import sys
import httpx


async def test(host, username, password, torrent_url):
    c = httpx.AsyncClient(timeout=30, verify=False)
    api = f"{host}/webapi/entry.cgi"

    # 1. 登录
    r = await c.get(api, params={
        "api": "SYNO.API.Auth", "version": "6", "method": "login",
        "account": username, "passwd": password,
        "session": "DownloadStation", "format": "sid",
    })
    login = r.json()
    if not login.get("success"):
        print("登录失败:", login)
        return
    sid = login["data"]["sid"]
    print("登录成功, sid:", sid[:20] + "...")

    # 2. 获取默认目录
    r = await c.get(api, params={
        "api": "SYNO.DownloadStation2.Settings.Location", "version": "1",
        "method": "get", "_sid": sid,
    })
    loc = r.json()
    dest = loc.get("data", {}).get("default_destination", "")
    print("默认目录:", dest)

    # 3. 尝试多种参数格式创建 URL 任务
    formats = [
        {"label": "格式A: uri=json数组, destination=字符串",
         "data": {"api": "SYNO.DownloadStation2.Task", "version": "2",
                  "method": "create", "type": "url",
                  "uri": json.dumps([torrent_url]),
                  "destination": dest, "create_list": "false", "_sid": sid}},
        {"label": "格式B: url=json数组 (改字段名)",
         "data": {"api": "SYNO.DownloadStation2.Task", "version": "2",
                  "method": "create", "type": "url",
                  "url": json.dumps([torrent_url]),
                  "destination": dest, "create_list": "false", "_sid": sid}},
        {"label": "格式C: uri=纯字符串",
         "data": {"api": "SYNO.DownloadStation2.Task", "version": "2",
                  "method": "create", "type": "url",
                  "uri": torrent_url,
                  "destination": dest, "create_list": "false", "_sid": sid}},
        {"label": "格式D: 用v1 API",
         "data": {"api": "SYNO.DownloadStation.Task", "version": "1",
                  "method": "create", "uri": torrent_url, "_sid": sid}},
    ]

    for fmt in formats:
        print(f"\n--- {fmt['label']} ---")
        r = await c.post(api, data=fmt["data"])
        print("响应:", r.text[:300])
        if r.json().get("success"):
            print(">>> 成功!")
            break

    await c.aclose()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"
    user = sys.argv[2] if len(sys.argv) > 2 else "admin"
    pwd = sys.argv[3] if len(sys.argv) > 3 else "admin"
    url = sys.argv[4] if len(sys.argv) > 4 else "https://www.example.com/test.torrent"
    asyncio.run(test(host, user, pwd, url))
