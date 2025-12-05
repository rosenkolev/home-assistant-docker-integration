import asyncio

import aiohttp


async def check_ghcr(image_name):
    print(f"Checking {image_name}")
    async with aiohttp.ClientSession() as session:
        # 1. Parse (Simplified from _docker_api.py)
        parts = image_name.split("/")
        registry = parts[0]
        repository = "/".join(parts[1:])
        if ":" in repository:
            repository, tag = repository.split(":", 1)
        else:
            tag = "latest"

        print(f"Registry: {registry}, Repo: {repository}, Tag: {tag}")

        # 2. Auth
        auth_url = f"https://{registry}/v2/"
        print(f"GET {auth_url}")
        async with session.get(auth_url) as resp:
            print(f"Auth loop status: {resp.status}")
            if resp.status == 401:
                # auth_header = resp.headers.get("Www-Authenticate")
                # print(f"Www-Authenticate: {auth_header}")

                # Parse auth header
                # auth_header = auth_header.replace("Bearer ", "")
                # params = {}
                # for part in auth_header.split(","):
                #    if "=" in part:
                #        key, value = part.strip().split("=", 1)
                #        params[key] = value.strip('"')

                realm = "https://ghcr.io/token"  # params.get("realm")
                service = "ghcr.io"  # params.get("service")
                # ORIGINAL LOGIC: prefer header scope
                scope = "repository:immich-app/immich-server:pull"  # params.get("scope") or f"repository:{repository}:pull"

                token_url = f"{realm}?service={service}&scope={scope}"
                print(f"Token URL: {token_url}")
                async with session.get(token_url) as token_resp:
                    token_data = await token_resp.json()
                    token = token_data.get("token") or token_data.get("access_token")
                    print(f"Got token: {token[:10]}...")
            else:
                print("No 401, unexpected")
                return

        # 3. Manifest
        api_base = f"https://{registry}/v2/{repository}"
        headers = {"Authorization": f"Bearer {token}"}
        # UPDATED headers
        headers["Accept"] = (
            "application/vnd.docker.distribution.manifest.v2+json, "
            "application/vnd.oci.image.manifest.v1+json, "
            "application/vnd.docker.distribution.manifest.list.v2+json, "
            "application/vnd.oci.image.index.v1+json"
        )

        manifest_url = f"{api_base}/manifests/{tag}"
        print(f"GET {manifest_url}")
        async with session.get(manifest_url, headers=headers) as resp:
            print(f"Manifest Status: {resp.status}")
            print(f"Headers: {resp.headers}")
            if resp.status != 200:
                print(await resp.text())
            else:
                data = await resp.json()
                print("Success! Manifest keys:", data.keys())
                print(data)


if __name__ == "__main__":
    asyncio.run(check_ghcr("ghcr.io/immich-app/immich-server:release"))
