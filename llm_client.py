import curses
from curses.textpad import Textbox
from enum import Enum
import subprocess
import getopt
import sys
import os
import math
import glob
import requests
import json
import threading
import queue
import time
import datetime

rootPath = "data"
promptTemplateFilePath = rootPath + "/prompt_template"
continuationPromptTemplateFilePath = rootPath + "/continuation_prompt_template"
promptFilePath = rootPath + "/prompt"
saveFilePath = rootPath + "/save"
filesFilePath = rootPath + "/files"
completionFilePath = rootPath + "/completion"

workingDirectory = "."

baseUrl = "http://localhost:8080"
completionUrl = baseUrl + "/completion"
healthUrl = baseUrl + "/health"
propsUrl = baseUrl + "/props"
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
    return "Offline"

def checkModel():
    try:
        with requests.get(propsUrl, headers=headers) as response:
            response.raise_for_status()
            try:
                return str(response.json()["model_path"]), response.json()["default_generation_settings"]["n_ctx"]
            except json.JSONDecodeError:
                pass
    except Exception as e:
        pass
    return "-", 0

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

def completionFinished(continuationPromptTemplate, prompt):
    return (continuationPromptTemplate in prompt)

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

def editTextBox(target, serverStatus, modelPath, contextWindow, fileNames, windowWidth, windowHeight, coloursEnabled, promptInput):
    inputStartLine = 5
    fileList = ""
    addStr(target, 0, "Server: " + serverStatus, Colour.WHITE.value, coloursEnabled)
    addStr(target, 1, "Model: " + modelPath, Colour.WHITE.value, coloursEnabled)
    addStr(target, 2, "Context Window: " + str(contextWindow), Colour.WHITE.value, coloursEnabled)
    if promptInput:
        inputStartLine = 6
        fileList = formatList(fileNames)
        addStr(target, 4, "Prompt: ", Colour.WHITE.value, coloursEnabled)
    addStr(target, 3, "Files: " + fileList, Colour.WHITE.value, coloursEnabled)
    editWindowWidth = min(100, windowWidth)
    editWindowHeight = min(100, windowHeight)
    editWindow = curses.newwin(editWindowHeight-2, editWindowWidth-2, inputStartLine, 0)
    target.move(0, 0)
    editWindow.refresh()
    target.refresh()
    box = Textbox(editWindow)
    box.edit(validator)
    text = box.gather()
    target.erase()
    return text

def formatTime(timeInSeconds):
    return str(datetime.timedelta(seconds=timeInSeconds)).split(".")[0]

def getSpinnerString():
    dt = datetime.datetime.now()
    ms = dt.microsecond / 1000 % 1000
    if ms < 250:
        return "."
    if ms < 500:
        return ".."
    if ms < 750:
        return "..."
    return "...."

def renderHorizontalLine(target, y, coloursEnabled):
    lineString = "─" * curses.COLS
    addStr(target, y, lineString, Colour.WHITE.value, coloursEnabled)

def stringLineCount(string):
    return max(1, math.ceil(len(string) / curses.COLS))

def lineNumberPadding(lineNumber, lineCount):
    return " " * (len(str(lineCount - 1)) - len(str(lineNumber)))

def copyToClipboard(string):
    try:
        subprocess.run(["xclip", "-selection", "clipboard"], input=string.encode("utf8"))
    except:
        pass

def listToString(outputLines, selectStartLine, selectEndLine):
    string = ""
    if selectStartLine < selectEndLine:
        for i in range(selectStartLine, selectEndLine + 1):
            string += outputLines[i] + "\n"
    else:
        for i in range(selectEndLine, selectStartLine + 1):
            string += outputLines[i] + "\n"
    return string

def readFile(path):
    try:
        with open(path, "r") as file:
            return file.read(), True
    except FileNotFoundError:
        return "FileNotFoundError: " + path, False
    except PermissionError:
        return "PermissionError: " + path, False
    except Exception as e:
        return "Error: " + str(e), False
    return "", False

def main(stdscr, continueMode):

    coloursEnabled = False
    scrollSpeed = 5
    headerHeight = 3

    scrollState = 0
    cursorPosition = 0
    selectMode = False
    selectStartLine = 0
    selectEndLine = 0
    tokensPredicted = 0
    tokensEvaluated = 0
    modelThinkEndLine = -1

    stdscr = curses.initscr()
    stdscr.clear()
    stdscr.keypad(True)
    stdscr.idlok(True)
    windowHeight, windowWidth = curses.LINES, curses.COLS

    if curses.has_colors() and curses.COLORS >= 244:
        coloursEnabled = True
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(Colour.WHITE.value, 7, -1)
        curses.init_pair(Colour.GREY.value, 244, -1)
        curses.init_pair(Colour.GREEN.value, 2, -1)
        curses.init_pair(Colour.HIGHLIGHT.value, 0, 2)

    serverStatus = str(checkServerStatus())
    modelPath, contextWindow = checkModel()

    outputLines = []
    userPrompt = ""
    prompt = ""

    promptTemplate = "<USERPROMPT>"
    result, success = readFile(promptTemplateFilePath)
    if success:
        promptTemplate = result
    else:
        outputLines.append(result)
    continuationPromptTemplate = "<USERPROMPT>"
    result, success = readFile(continuationPromptTemplateFilePath)
    if success:
        continuationPromptTemplate = result
    else:
        outputLines.append(result)


    fileNames = []
    modelStartLine = 0
    if continueMode:
        result, success = readFile(promptFilePath)
        if success:
            userPrompt = result
        else:
            outputLines.append(result)
        result, success = readFile(saveFilePath)
        if success:
            modelStartLine = int(result)
        else:
            outputLines.append(result)
        result, success = readFile(filesFilePath)
        if success:
            fileNames = json.loads(str(result).replace('\'', '"'))
        else:
            outputLines.append(result)
        result, success = readFile(completionFilePath)
        if success:
            prompt = result
        else:
            outputLines.append(result)
    else:
        patterns = editTextBox(stdscr, serverStatus, modelPath, contextWindow, [], windowWidth, windowHeight, coloursEnabled, False)
        patterns = patterns.split(" ")
        if patterns != [""]:
            for pattern in patterns:
                pattern = pattern.strip()
                if pattern != "":
                    globbedNames = glob.glob(workingDirectory + "/" + pattern)
                    for name in globbedNames:
                        if not os.path.isdir(name):
                            fileNames.append(name)

    if not continueMode:
        userPrompt = editTextBox(stdscr, serverStatus, modelPath, contextWindow, fileNames, windowWidth, windowHeight, coloursEnabled, True)
        attachmentPrompt = userPrompt
        if len(fileNames) > 0:
            attachmentPrompt += "\nUse the following file(s): \n"
            for fileName in fileNames:
                try:
                    with open(fileName, "r", encoding="utf-8") as file:
                        attachmentPrompt += "<FILE START: " + fileName + ">\n"
                        attachmentPrompt += file.read()
                        attachmentPrompt += "<FILE END: " + fileName + ">\n\n"
                except FileNotFoundError:
                    outputLines.append("FileNotFoundError: " + fileName)
                except PermissionError:
                    outputLines.append("PermissionError: " + fileName)
                except Exception as e:
                    outputLines.append("Error: " + str(e))
        prompt = promptTemplate.replace("<USERPROMPT>", attachmentPrompt)
    elif completionFinished(continuationPromptTemplate, prompt):
        userPrompt = editTextBox(stdscr, serverStatus, modelPath, contextWindow, fileNames, windowWidth, windowHeight, coloursEnabled, True)
        prompt = prompt.replace("<USERPROMPT>", userPrompt)

    userPromptLines = userPrompt.split("\n")
    for line in userPromptLines:
        outputLines.append(line)
    for name in fileNames:
        if continueMode:
            outputLines.insert(modelStartLine - len(fileNames) - 1, name)
        else:
            outputLines.append(name)

    if not continueMode:
        outputLines.append("")
        modelStartLine = len(outputLines) - 1
    else:
        outputLines.insert(modelStartLine - len(fileNames) - 1, "")

    requestFinished = False

    try:
        messageWindowWidth = windowWidth
        messageWindowHeight = windowHeight - headerHeight
        messageWindow = curses.newwin(messageWindowHeight, messageWindowWidth, headerHeight, 0)
        stdscr.move(0, 0)
        messageWindow.refresh()
        stdscr.refresh()

        curses.curs_set(0)
        stdscr.nodelay(True)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)

        modelCompletionStarted = False
        payload = {"prompt": prompt, "stream": True}
        q = queue.Queue()
        stop_event = threading.Event()
        t = threading.Thread(target=streamPost, args=(q, stop_event, payload), daemon=True)
        t.start()
        startTime = time.time()
        elapsedTime = formatTime(0)

        while(True):
            if not requestFinished:
                elapsedTime = formatTime(time.time() - startTime)
            curses.update_lines_cols()
            windowHeight, windowWidth = curses.LINES, curses.COLS
            rerender = False
            try:
                data = q.get(block = False)
                if data != None:
                    try:
                        dataContent = data["content"]
                        userPrompt += dataContent
                        prompt += dataContent
                        outputLines[-1] += dataContent
                        if "\n" in data["content"]:
                            outputLines.append("")
                        tokensPredicted = data["tokens_predicted"]
                        tokensEvaluated = data["tokens_evaluated"]

                        modelCompletionStarted = True
                        rerender = True
                    except Exception:
                        pass
                else:
                    requestFinished = True
                    t.join(timeout=0)
            except queue.Empty:
                pass

            ch = stdscr.getch()
            try:
                down = False
                up = False
                scrollDown = False
                scrollUp = False
                scrollFirst = False
                scrollLast = False
                if ch == curses.KEY_MOUSE:
                    _, _, _, _, bstate = curses.getmouse()
                    if bstate & curses.BUTTON5_PRESSED:
                        scrollDown = True
                    elif bstate & curses.BUTTON4_PRESSED:
                        scrollUp = True
                elif ch == curses.KEY_NPAGE or ch == 4:
                    scrollDown = True
                elif ch == curses.KEY_PPAGE or ch == 21:
                    scrollUp = True
                elif ch == curses.KEY_DOWN or ch == 106:
                    down = True
                elif ch == curses.KEY_UP or ch == 107:
                    up = True
                elif ch == 118:
                    selectMode = not selectMode
                    if selectMode:
                        selectStartLine = cursorPosition
                        selectEndLine = cursorPosition
                elif ch == 121:
                    if selectMode:
                        copyToClipboard(listToString(outputLines, selectStartLine, selectEndLine))
                        selectMode = False
                    else:
                        copyToClipboard(outputLines[cursorPosition])
                elif ch == 103:
                    scrollFirst = True
                elif ch == 71:
                    scrollLast = True
                elif ch == 27:
                    selectMode = False

                if down:
                    if cursorPosition < len(outputLines) - 1:
                        cursorPosition += 1
                        selectEndLine = cursorPosition
                        rerender = True
                if up:
                    if cursorPosition > 0:
                        cursorPosition -= 1
                        selectEndLine = cursorPosition
                        rerender = True
                if scrollDown:
                    scrollState +=scrollSpeed
                    cursorPosition += scrollSpeed
                    cursorPosition = min(cursorPosition, len(outputLines) - 1)
                    selectEndLine = cursorPosition
                    rerender = True
                if scrollUp:
                    scrollState -=scrollSpeed
                    cursorPosition -= scrollSpeed
                    cursorPosition = max(cursorPosition, 0)
                    selectEndLine = cursorPosition
                    rerender = True
                if scrollFirst:
                    cursorPosition = 0
                    scrollState = 0
                    selectEndLine = cursorPosition
                    rerender = True
                if scrollLast:
                    cursorPosition = len(outputLines) - 1
                    scrollState = cursorPosition
                    selectEndLine = cursorPosition
                    rerender = True
            except curses.error:
                pass

            tokensUsed = tokensPredicted+tokensEvaluated
            contextText = ""
            if contextWindow > 0:
                contextText = str(tokensUsed) + "/" + str(contextWindow) + " | " + str(tokensUsed/contextWindow*100).split(".")[0] + "% | " + elapsedTime
            else:
                contextText = "Offline"
            addStr(stdscr, 0, contextText, Colour.WHITE.value, coloursEnabled)
            renderHorizontalLine(stdscr, 1, coloursEnabled)
            if not modelCompletionStarted:
                addStr(stdscr, 2, getSpinnerString(), Colour.WHITE.value, coloursEnabled)
                rerender = True

            renderAreaHeight = windowHeight - headerHeight - 1
            lineCount = len(outputLines)

            scrollState = min(scrollState, cursorPosition)
            scrollState = max(scrollState, cursorPosition - renderAreaHeight)
            if lineCount - renderAreaHeight > 0:
                scrollState = min(scrollState, lineCount - renderAreaHeight)
            else:
                scrollState = 0
            scrollState = max(scrollState, 0)

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
                    if selectMode:
                        if selectStartLine < selectEndLine:
                            if i >= selectStartLine and i <= selectEndLine:
                                colour = Colour.HIGHLIGHT.value
                        else:
                            if i >= selectEndLine and i <= selectStartLine:
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
        try:
            with open(promptFilePath, "w") as file:
                file.write(userPrompt)
        except:
            pass
        try:
            with open(saveFilePath, "w") as file:
                file.write(str(modelStartLine))
        except:
            pass
        try:
            with open(filesFilePath, "w") as file:
                file.write(str(fileNames))
        except:
            pass
        try:
            with open(completionFilePath, "w") as file:
                file.write(prompt)
                if requestFinished:
                    file.write(continuationPromptTemplate)
        except:
            pass
        sys.exit(0)

continueMode = False
args = sys.argv[1:]
options = "hc"
long_options = ["help", "continue"]

try:
    arguments, values = getopt.getopt(args, options, long_options)
    for currentArg, currentVal in arguments:
        if currentArg in ("-h", "--help"):
            print("-c --continue Continue a previously unfinished completion or start a new user turn if finished")
        elif currentArg in ("-c", "--continue"):
            continueMode = True
except getopt.error as err:
    print(str(err))

curses.wrapper(main, continueMode)
