import asyncio
import os
import time

import aiofiles
import aiohttp
import requests
import tenacity
from result import Err, Ok

from ..messages import *

ghPrefix = "https://raw.githubusercontent.com/tabatkins/bikeshed-boilerplate/master/"


def update(path, dryRun=False):
    try:
        say("Downloading boilerplates...")
        data = requests.get(ghPrefix + "manifest.txt").text
    except Exception as e:
        die("Couldn't download boilerplates manifest.\n{0}", e)
        return

    newPaths = pathsFromManifest(data)

    if not dryRun:
        say(
            "Updating {0} file{1}...",
            len(newPaths),
            "s" if len(newPaths) > 1 else "",
        )
        goodPaths, badPaths = asyncio.run(updateFiles(path, newPaths))
    if not badPaths:
        say("Done!")
        return set(goodPaths)
    else:
        phrase = f"were {len(badPaths)} errors" if len(badPaths) > 1 else "was 1 error"
        die(
            f"Done, but there {phrase} (of {len(newPaths)} total) in downloading or saving. Run `bikeshed update` again to retry."
        )
        return set(goodPaths)


def pathsFromManifest(manifest):
    lines = manifest.split("\n")[1:]
    return [line.partition(" ")[2] for line in lines if line != ""]


async def updateFiles(localPrefix, newPaths):
    tasks = set()
    async with aiohttp.ClientSession() as session:
        for filePath in newPaths:
            coro = updateFile(localPrefix, filePath, session=session)
            tasks.add(coro)

        lastMsgTime = time.time()
        messageDelta = 2
        goodPaths = []
        badPaths = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result.is_ok():
                goodPaths.append(result.value)
            else:
                badPaths.append(result.value)
            currFileTime = time.time()
            if (currFileTime - lastMsgTime) >= messageDelta:
                if not badPaths:
                    say("Updated {0}/{1}...", len(goodPaths), len(newPaths))
                else:
                    say(
                        "Updated {0}/{1}, {2} errors...",
                        len(goodPaths),
                        len(newPaths),
                        len(badPaths),
                    )
                lastMsgTime = currFileTime
    return goodPaths, badPaths


async def updateFile(localPrefix, filePath, session):
    remotePath = ghPrefix + filePath
    localPath = localizePath(localPrefix, filePath)
    res = await downloadFile(remotePath, session)
    if res.is_ok():
        res = await saveFile(localPath, res.ok())
    else:
        warn(f"Error downloading {filePath}, full error was:\n{await errorFromAsyncErr(res)}")
    if res.is_err():
        res = Err(filePath)
    return res


async def errorFromAsyncErr(res):
    if res.is_ok():
        return res.ok()
    try:
        await res.err()
    except Exception as e:
        return e


def wrapError(retry_state):
    return Err(asyncio.wrap_future(retry_state.outcome))


@tenacity.retry(
    reraise=True,
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_random(1, 2),
    retry_error_callback=wrapError,
)
async def downloadFile(path, session):
    resp = await session.request(method="GET", url=path)
    resp.raise_for_status()
    return Ok(await resp.text())


@tenacity.retry(
    reraise=True,
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_random(1, 2),
    retry_error_callback=wrapError,
)
async def saveFile(path, data):
    dirPath = os.path.dirname(path)
    if not os.path.exists(dirPath):
        os.makedirs(dirPath)
    async with aiofiles.open(path, "w", encoding="utf-8") as fh:
        await fh.write(data)
        return Ok(path)


def localizePath(root, relPath):
    return os.path.join(root, *relPath.split("/"))
