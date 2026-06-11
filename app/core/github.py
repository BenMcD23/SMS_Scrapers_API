"""Helpers for committing files into our GitHub repos."""

import base64

import httpx
from fastapi import HTTPException

from core.config import GITHUB_TOKEN


def github_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


async def commit_files_to_github(
    client: httpx.AsyncClient,
    repo: str,
    branch: str,
    message: str,
    files: list[dict],
    delete_paths: list[str] = None,
):
    """Add/update `files` and remove `delete_paths` in a single commit via the Git Data API.

    `files` is a list of {"path": str, "content": bytes}. Using blobs + a tree + one
    commit avoids the Contents API's one-commit-per-file behaviour.
    """
    headers = github_headers()
    base = f"https://api.github.com/repos/{repo}"

    # Current branch head and its tree
    ref_resp = await client.get(f"{base}/git/ref/heads/{branch}", headers=headers)
    if ref_resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Could not read {branch} ref: {ref_resp.text}")
    latest_commit_sha = ref_resp.json()["object"]["sha"]

    commit_resp = await client.get(f"{base}/git/commits/{latest_commit_sha}", headers=headers)
    if commit_resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Could not read base commit: {commit_resp.text}")
    base_tree_sha = commit_resp.json()["tree"]["sha"]

    # Upload each file as a blob and collect tree entries
    tree_entries = []
    for f in files:
        blob_resp = await client.post(
            f"{base}/git/blobs",
            headers=headers,
            json={"content": base64.b64encode(f["content"]).decode("utf-8"), "encoding": "base64"},
        )
        if blob_resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"Blob upload failed for {f['path']}: {blob_resp.text}")
        tree_entries.append({"path": f["path"], "mode": "100644", "type": "blob", "sha": blob_resp.json()["sha"]})

    # Deletions: a null sha removes the path from the new tree
    for path in (delete_paths or []):
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})

    tree_resp = await client.post(
        f"{base}/git/trees",
        headers=headers,
        json={"base_tree": base_tree_sha, "tree": tree_entries},
    )
    if tree_resp.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"Tree creation failed: {tree_resp.text}")

    new_commit = await client.post(
        f"{base}/git/commits",
        headers=headers,
        json={"message": message, "tree": tree_resp.json()["sha"], "parents": [latest_commit_sha]},
    )
    if new_commit.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"Commit creation failed: {new_commit.text}")

    update_ref = await client.patch(
        f"{base}/git/refs/heads/{branch}",
        headers=headers,
        json={"sha": new_commit.json()["sha"]},
    )
    if update_ref.status_code not in (200, 201):
        raise HTTPException(status_code=500, detail=f"Ref update failed: {update_ref.text}")
