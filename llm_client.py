import curses
from curses.textpad import Textbox
from enum import Enum
import sys
import os
import math
import glob
import requests
import json
import threading
import queue
import time

rootPath = "data"
promptTemplateFilePath = rootPath + "/prompt_template"
workingDirectory = "."

baseUrl = "http://localhost:8080"
completionUrl = baseUrl + "/completion"
healthUrl = baseUrl + "/health"
headers = {"Content-Type": "application/json"}

class Colour(Enum):
    WHITE = 1
    GREY = 2
    GREEN = 3
    HIGHLIGHT = 4

def checkServerStatus():
    try:
        with requests.get(healthUrl, headers=headers) as response:
            response.raise_for_status()
            try:
                return response.json()
            except json.JSONDecodeError:
                pass
    except Exception as e:
        pass

def streamPost(q, stop_event, payload):
    try:
        with requests.post(completionUrl, headers=headers, json=payload, stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if stop_event.is_set():
                    break
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if line == "[DONE]":
                    break
                try:
                    q.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        pass
    finally:
        q.put(None)

def validator(ch):
    if ch == ord("\n"):
        return curses.ascii.BEL
    return ch

def formatList(value):
    string = ""
    for i in value:
        string += str(i) + " "
    return string

def addStr(target, y, string, colour, coloursEnabled):
    if coloursEnabled:
        target.addstr(y, 0, string, curses.color_pair(colour))
    else:
        target.addstr(y, 0, string)

def renderHorizontalLine(target, y, coloursEnabled):
    lineString = "─" * curses.COLS
    addStr(target, y, lineString, Colour.WHITE.value, coloursEnabled)

def stringLineCount(string):
    return max(1, math.ceil(len(string) / curses.COLS))

def lineNumberPadding(lineNumber, lineCount):
    return " " * (len(str(lineCount - 1)) - len(str(lineNumber)))

def main(stdscr, serverStatus):

    coloursEnabled = False
    scrollSpeed = 5
    headerHeight = 3

    scrollState = 0
    cursorPosition = 0
    tokensPredicted = 0
    tokensEvaluated = 0
    modelThinkEndLine = -1

    stdscr = curses.initscr()
    stdscr.clear()
    windowHeight, windowWidth = curses.LINES, curses.COLS

    if curses.has_colors() and curses.COLORS >= 244:
        coloursEnabled = True
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(Colour.WHITE.value, 7, -1)
        curses.init_pair(Colour.GREY.value, 244, -1)
        curses.init_pair(Colour.GREEN.value, 2, -1)
        curses.init_pair(Colour.HIGHLIGHT.value, 0, 2)

    outputLines = []

    addStr(stdscr, 0, "Server: " + serverStatus, Colour.WHITE.value, coloursEnabled)
    addStr(stdscr, 1, "Files: ", Colour.WHITE.value, coloursEnabled)
    editWindowWidth = min(100, windowWidth)
    editWindowHeight = min(100, windowHeight)
    editWindow = curses.newwin(editWindowHeight-2, editWindowWidth-2, 3, 0)
    editWindow.refresh()
    stdscr.refresh()
    box = Textbox(editWindow)
    box.edit(validator)
    patterns = box.gather()
    patterns = patterns.split(" ")
    fileNames = []
    if patterns != [""]:
        for pattern in patterns:
            pattern = pattern.strip()
            if pattern != "":
                globbedNames = glob.glob(workingDirectory + "/" + pattern)
                for name in globbedNames:
                    if not os.path.isdir(name):
                        fileNames.append(name)

    stdscr.erase()
    addStr(stdscr, 0, "Server: " + serverStatus, Colour.WHITE.value, coloursEnabled)
    addStr(stdscr, 1, "Files: " + formatList(fileNames), Colour.WHITE.value, coloursEnabled)
    addStr(stdscr, 2, "Prompt: ", Colour.WHITE.value, coloursEnabled)
    editWindow = curses.newwin(editWindowHeight-2, editWindowWidth-2, 4, 0)
    stdscr.move(0, 0)
    editWindow.refresh()
    stdscr.refresh()
    box = Textbox(editWindow)
    box.edit(validator)
    userPrompt = box.gather()
    stdscr.erase()

    userPromptLines = userPrompt.split("\n")
    for line in userPromptLines:
        outputLines.append(line)

    for name in fileNames:
        outputLines.append(name)

    attachmentPrompt = userPrompt
    if len(fileNames) > 0:
        attachmentPrompt += "\nUse the following file(s): \n"
        for fileName in fileNames:
            try:
                with open(fileName, "r", encoding="utf-8") as f:
                    attachmentPrompt += "<FILE START: " + fileName + ">\n"
                    attachmentPrompt += f.read()
                    attachmentPrompt += "<FILE END: " + fileName + ">\n\n"
            except FileNotFoundError:
                outputLines.append("FileNotFoundError: " + fileName)
            except PermissionError:
                outputLines.append("PermissionError: " + fileName)
            except Exception as e:
                outputLines.append("Error: " + str(e))

    prompt = promptTemplate.replace("<USERPROMPT>", attachmentPrompt)

    outputLines.append("")
    modelStartLine = len(outputLines)

    try:
        messageWindowWidth = windowWidth
        messageWindowHeight = windowHeight - headerHeight
        messageWindow = curses.newwin(messageWindowHeight, messageWindowWidth, headerHeight, 0)
        stdscr.move(0, 0)
        messageWindow.refresh()
        stdscr.refresh()
        box = Textbox(messageWindow)

        messageWindow.keypad(True)
        messageWindow.idlok(True)
        curses.curs_set(0)
        stdscr.nodelay(True)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)

        payload = {"prompt": prompt, "stream": True}
        q = queue.Queue()
        stop_event = threading.Event()
        t = threading.Thread(target=streamPost, args=(q, stop_event, payload), daemon=True)
        t.start()

        while(True):
            curses.update_lines_cols()
            windowHeight, windowWidth = curses.LINES, curses.COLS
            rerender = False
            try:
                data = q.get(block = False)
                if data != None:
                    try:
                        outputLines[-1] += data["content"]
                        if "\n" in data["content"]:
                            outputLines.append("")
                        tokensPredicted = data["tokens_predicted"]
                        tokensEvaluated = data["tokens_evaluated"]

                        rerender = True
                    except Exception:
                        pass
                else:
                    t.join(timeout=0)
            except queue.Empty:
                pass

            ch = stdscr.getch()
            try:
                if ch == curses.KEY_MOUSE:
                    _, _, _, _, bstate = curses.getmouse()
                    if bstate & curses.BUTTON4_PRESSED:
                        scrollState -= scrollSpeed
                        rerender = True
                    elif bstate & curses.BUTTON5_PRESSED:
                        scrollState +=scrollSpeed
                        rerender = True
                elif ch != -1 and 0 <= ch <= 255:
                    if ch == curses.KEY_NPAGE:
                        scrollState -=scrollSpeed
                        rerender = True
                    elif ch == curses.KEY_PPAGE:
                        scrollState +=scrollSpeed
                        rerender = True
                    elif ch == curses.KEY_DOWN or ch == 106:
                        cursorPosition += 1
                        rerender = True
                    elif ch == curses.KEY_UP or ch == 107:
                        cursorPosition -= 1
                        rerender = True
            except curses.error:
                pass

            addStr(stdscr, 0, "context size: " + str(tokensPredicted+tokensEvaluated), Colour.WHITE.value, coloursEnabled)
            renderHorizontalLine(stdscr, 1, coloursEnabled)

            renderAreaHeight = windowHeight - headerHeight - 1
            lineCount = len(outputLines)

            scrollState = min(scrollState, lineCount)
            scrollState = max(0, scrollState)

            y = 0
            for i in range(lineCount):
                if modelThinkEndLine == -1 and "</think>" in outputLines[i]:
                    modelThinkEndLine = i
                if scrollState <= i <= scrollState + renderAreaHeight:
                    readLine = outputLines[i]
                    colour = Colour.GREEN.value
                    if i >= modelStartLine:
                        colour = Colour.GREY.value
                    if modelThinkEndLine != -1 and i > modelThinkEndLine:
                        colour = Colour.WHITE.value
                    if i == cursorPosition:
                        colour = Colour.HIGHLIGHT.value
                    addStr(messageWindow, y, str(i) + lineNumberPadding(i, lineCount) + " " + readLine, colour, coloursEnabled)
                    y += stringLineCount(readLine)

            messageWindow.refresh()
            stdscr.refresh()
            if rerender:
                messageWindow.erase()
                stdscr.erase()
            time.sleep(0.02)

    except KeyboardInterrupt:
        t.join(timeout=0)
        sys.exit(0)

serverStatus = str(checkServerStatus())

with open(promptTemplateFilePath, "r") as file:
    promptTemplate = file.read()

curses.wrapper(main, serverStatus)
