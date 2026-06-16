import curses
from curses.textpad import Textbox
import sys
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

url = "http://localhost:8080/completion"
headers = {"Content-Type": "application/json"}

def stream_post(q, stop_event, payload):
    try:
        with requests.post(url, headers=headers, json=payload, stream=True) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
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

def renderHorizontalLine(stdscr, y):
    lineString = ""
    for i in range(curses.COLS):
        lineString += "─"
    stdscr.addstr(y, 0, lineString)

def stringLineCount(string):
    return max(1, math.ceil(len(string) / curses.COLS))

def main(stdscr, fileNames, userPrompt, prompt):

    scrollSpeed = 5
    headerHeight = 3

    scrollState = 0
    cursorPosition = 0
    tokensPredicted = 0
    tokensEvaluated = 0

    stdscr = curses.initscr()
    stdscr.clear()
    windowHeight, windowWidth = curses.LINES, curses.COLS

    outputLines = []
    userPromptLines = userPrompt.split("\n")
    for line in userPromptLines:
        outputLines.append(line)
    outputLines.append("")
    outputLines.append("")

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
        t = threading.Thread(target=stream_post, args=(q, stop_event, payload), daemon=True)
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
                    stdscr.addstr(0, 0, "context: " + str(ch))
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

            stdscr.addstr(0, 0, "project context: " + str(fileNames))
            stdscr.addstr(1, 0, "context size: " + str(tokensPredicted+tokensEvaluated))
            renderHorizontalLine(stdscr, 2)

            renderAreaHeight = windowHeight - headerHeight
            lineCount = len(outputLines)

            scrollState = min(scrollState, lineCount)
            scrollState = max(0, scrollState)

            y = 0
            for i in range(lineCount):
                messageWindow.standend()
                if i == cursorPosition:
                    messageWindow.standout()
                if scrollState <= i <= scrollState + renderAreaHeight:
                    readLine = outputLines[i]
                    messageWindow.addstr(y, 0, str(i) + " " + readLine)
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

with open(promptTemplateFilePath, "r") as file:
    promptTemplate = file.read()

patterns = input("Files: ")
patterns = patterns.split(" ")
fileNames = []
if patterns != [""]:
    for pattern in patterns:
        fileNames += glob.glob(workingDirectory+"/*"+pattern+"*")

for fileName in fileNames:
    print(fileName)

userPrompt = input("Prompt: ")
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
            print("FileNotFoundError")
        except PermissionError:
            print("PermissionError")
        except Exception as e:
            print(f"Error: {e}")

prompt = promptTemplate.replace("<USERPROMPT>", attachmentPrompt)

curses.wrapper(main, fileNames, userPrompt, prompt)
