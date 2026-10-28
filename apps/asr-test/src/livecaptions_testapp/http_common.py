from fastapi import HTTPException, Request


def same_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "接口只接受本机测试页的同源请求。")
