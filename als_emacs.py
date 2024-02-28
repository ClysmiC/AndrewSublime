# TODO
# - EXPERIMENT: Add a hotkey (or implement) to "shift selection left/right a character/word", only works if selection is on single line
# - change name from i-search to r-search when reverse-searching? (or i-search (r) ?)
#   ... not sure if I can change panel name after opening it... not sure I'd want this in status bar either =/
# - If initial text matches something but then the text gets changed, start back from the beginning of the search
# - Make sure searches are highlighted in scroll bar (and maybe minimap?)
# - If active mark and then search forward a bunch of times and then backwards a bunch of times, shrink the selection back on the backwards searches
# - Ensure selecion is robust against wraparound search and wraparound reverse search
# - Better streamlined find/replace with yn.! options (see command mode? https://docs.sublimetext.io/reference/key_bindings.html#the-any-character-binding)
# - (Experiment) Drop mark after incremental search selection?
# - (Experiment) Consider always dropping a mark any time we have a selection? Why sholudn't shift insert a mark?
# - Mark ring (implemented, but currently dropped in favor of built-in jump functionality...)
# - Jump focus to other sublime window (not just other view)
# - Consolidate all open tabs to 1 window. Option to close duplicates?
# - Force h on left and cpp on right?
# - virtual whitespace? maybe remap ctrl-alt-arrow to navigate

import sublime
import sublime_plugin

from enum import Enum
import traceback #debug

import subprocess
import sys

from pathlib import Path
import os

# --- Logging

LOG_TO_FILE = False

LOG_DEBUG				= None #or "debug"
LOG_INIT				= None #or "init"
LOG_HIDE_PANEL_THEN_RUN = None #or "als_hide_panel_then_run"
LOG_EVENTS				= None #or "event listener"
LOG_ISEARCH				= None #or "i-search"
LOG_BUILD				= None #or "build"
LOG_MARK_RING			= None #or "mark-ring"

def plugin_loaded():
	if LOG_TO_FILE:
		with open('plugin_trace.txt','w') as file:
		    pass	# NOTE - Clears file

def trace(tag, text):
	if tag:
		if LOG_TO_FILE:
			# SLOW - opening file every time we log, lol
			with open('plugin_trace.txt', 'a') as file:
				timestamp = time.time()
				timestamp %= 100
				timestamp *= 1000
				timestamp = round(timestamp)
				timestamp /= 1000
				file.write(f"{timestamp} :::: [{tag}] {text}\n")
		else:
			print(text)

# --- Extra state stored per view

class ViewEx():
	dictionary = {}

	def __init__(self, view):
		self.view = view
		self.markSel = MarkSel(view)

	@staticmethod
	def get(view):
		result = ViewEx.dictionary.get(view.id())
		if result is None:
			result = ViewEx(view)
			ViewEx.dictionary[view.id()] = result

		return result

	def onClose(self):
		# TODO - Hook this callback up to either on_close(..) or on_pre_close(..)
		ViewEx.dictionary.pop(self.view.id())

	def entireViewRegion(self):
		result = sublime.Region(0, self.view.size())
		return result


# --- Extra state stored per view window

class WindowEx():
	dictionary = {}

	def __init__(self, window):
		self.window = window
		self.iSearch = ISearch(window)
		self.inputPanel = InputPanel(window)

	@staticmethod
	def get(window):
		result = WindowEx.dictionary.get(window.id())
		if result is None:
			result = WindowEx(window)
			WindowEx.dictionary[window.id()] = result

		return result

	def onClose(self):
		# TODO - Hook this callback up to either on_close(..) or on_pre_close(..)
		WindowEx.dictionary.pop(self.window.id())

	def showCustomStatus(self, text):
		# HMM - Should we set it for all views? Weird that this is something we set per-view, despite being displayed per-window
		#	What about new views that get created afterwards...?
		view = self.window.active_view()
		view.set_status("als_padding", "------------------------")	# Unsure how to remove sublime's line/column text in the status bar`.... so I'll at least separate it from my statuses!
		view.set_status("als_custom", text)

	def clearCustomStatus(self):
		# HMM - Should we set it for all views? Weird that this is something we set per-view, despite being displayed per-window
		view = self.window.active_view()
		view.erase_status("als_padding")
		view.erase_status("als_custom")


# --- Mark/selection (similar to 'transient-mark-mode' in emacs)

class SelectionAction(Enum):
	CLEAR 					= 0
	KEEP 					= 1

class MarkAction(Enum):
	CLEAR					= 0
	KEEP					= 1 # NOTE - SETs if already active, CLEARs if otherwise
	SET						= 2 # NOTE - Doesn't add to ring... should it?

class MarkSel():

	@staticmethod
	def get(view):
		return ViewEx.get(view).markSel

	def __init__(self, view):
		self.view = view
		self.selection = view.sel()
		self.hiddenSelRegion = None			# Supports ISearch focus color not being obscured by a selected region
		# self.markRing = [-1] * 16
		# self.iMarkRingStart = 0
		# self.iMarkRingCycle = 0				# NOTE - Points where next mark will write. Also, what "next" should jump to.
		# self.iMarkRingEnd = 0
		self.clearMark()

	def clearMark(self):
		self.mark = -1
		self.wantIgnoreModification = 0 	# State for re-jiggering selection during various commands
		self.wantKeepMark = 0				# ...

	# --- Static utils

	@staticmethod
	def extendRegion(regionStart, regionExtendTo):
		isStartReversed = MarkSel.isRegionReversed(regionStart)
		result = regionStart.cover(regionExtendTo)

		if isStartReversed != MarkSel.isRegionReversed(result):
			result = MarkSel.reverseRegion(result)

		return result

	@staticmethod
	def isRegionReversed(region):
		return region.a > region.b

	@staticmethod
	def reverseRegion(region):
		return sublime.Region(region.b, region.a)

	@staticmethod
	def subtractRegion(region, regionToSubtract):
		isBeginningInsideRegion = regionToSubtract.begin() > region.begin() and regionToSubtract.begin() < region.end()
		isEndInsideRegion = regionToSubtract.end() > region.begin() and regionToSubtract.end() < region.end()

		if isBeginningInsideRegion and isEndInsideRegion:
			return [sublime.Region(region.begin(), regionToSubtract.begin()),
					sublime.Region(regionToSubtract.end(), region.end())]
		elif isBeginningInsideRegion:
			return [sublime.Region(region.begin(), regionToSubtract.begin())]
		elif isEndInsideRegion:
			return [sublime.Region(regionToSubtract.end(), region.end())]
		elif regionToSubtract.begin() <= region.begin() and regionToSubtract.end() >= region.end():
			return []
		else:
			return [sublime.Region(region.begin(), region.end())]

	# --- Operations

	def isSelectionHidden(self):
		return len(self.selection) == 0

	def hideSelection(self):
		self.hiddenSelRegion = self.primaryRegion()
		if len(self.selection) > 0:
			self.selection.clear()

	def showSelection(self):
		if len(self.selection) == 0:
			self.selection.add(self.hiddenSelRegion if self.hiddenSelRegion is not None else self.primaryRegion())

		self.hiddenSelRegion = None

	def primaryCursor(self):
		return self.primaryRegion().b

	def primaryRegion(self):
		if len(self.selection) > 0 and self.selection[0].a >= 0 and self.selection[0].b >= 0:
			return self.selection[0]
		elif self.isSelectionHidden() and self.hiddenSelRegion is not None:
			return self.hiddenSelRegion

		return sublime.Region(0, 0)		# fallback

	def isExactlyPrimaryRegion(self, region, ignoreReversedness=False):
		primaryRegion = self.primaryRegion()
		if ignoreReversedness:
			return region.begin() == primaryRegion.begin() and region.end() == primaryRegion.end()
		else:
			return region.a == primaryRegion.a and region.b == primaryRegion.b

	def isMarkActive(self):
		return self.mark >= 0

	# NOTE - clear with clearMark(..)
	def placeMark(self, selectionAction, addToMarkRing=True):
		self.mark = self.primaryCursor()

		if addToMarkRing:
			# self.addMarkToMarkRing()
			pass

		if selectionAction == SelectionAction.CLEAR:
			self.selection.clear()
			self.selection.add(sublime.Region(self.mark, self.mark))

		elif selectionAction == SelectionAction.KEEP:
			pass

	# NOTE - clear with clearAll(..) or placeMark(selectionAction=SelectionAction.CLEAR)
	def select(self, region, markAction, extend=False, wantShow=True):

		if extend:
			extendedRegion = MarkSel.extendRegion(self.primaryRegion(), region)
			self.selection.clear()
			self.selection.add(extendedRegion)
		else:
			self.selection.clear()
			self.selection.add(region)

		# HMM - Maybe we should only validate here...? Instead of *NOT* validating here lol
		if wantShow:
			self.view.show(region)

		if markAction == MarkAction.CLEAR:
			self.clearMark()

		elif (markAction == MarkAction.SET) or \
			(markAction == MarkAction.KEEP and self.isMarkActive()):
			self.mark = region.a

		return region

	def selectPrimaryRegion(self, markAction, extend=False, wantShow=True):
		return self.select(self.primaryRegion(), markAction, extend=extend, wantShow=wantShow)

	def clearAll(self):
		cursor = self.primaryCursor()
		self.select(sublime.Region(cursor, cursor), MarkAction.CLEAR)

	def isSingleNonEmptySelection(self):
		return len(self.selection) == 1 and self.selection[0].a != self.selection[0].b

	def isSingleEmptySelection(self):
		return len(self.selection) == 1 and self.selection[0].a == self.selection[0].b

	# --- MARK RING

	# TODO - Maintain historical mark positions even if the buffer has been edited? Emacs does this, but I'm
	#  not sure how it's implemented. Maybe sublime has something in its API I can use?

	# def addToMarkRing(self, iPos, ignoreDuplicate=True):
	# 	trace(True, f"Adding {iPos} to mark ring (i = {self.iMarkRingEnd}, view element = {self.view.element()}, view id = {self.view.id()}")

	# 	iPlace = self.iMarkRingCycle

	# 	# We may have just jumped to this index, so lets nudge to 1 past.
	# 	# HMM - Is this right?

	# 	if self.isIMarkRingValid(self.iMarkRingCycle):
	# 		iPlace += 1
	# 		iPlace %= len(self.markRing)

	# 	if ignoreDuplicate:
	# 		iPrev = self.iMarkRingCycle - 1
	# 		if iPrev < 0:
	# 			iPrev = len(self.markRing) - 1

	# 		if self.isIMarkRingValid(iPrev):
	# 			prevMark = self.markRing[iPrev]
	# 			if iPos == prevMark:
	# 				trace(True, f"Skipping duplicate addToMarkRing with iPos = {iPos} (same as mark i{iPrev})")
	# 				return

	# 	self.markRing[iPlace] = iPos
	# 	self.iMarkRingEnd = iPlace + 1
	# 	self.iMarkRingEnd %= len(self.markRing)
	# 	self.iMarkRingCycle = self.iMarkRingEnd

	# 	if self.iMarkRingEnd == self.iMarkRingStart:
	# 		self.iMarkRingStart += 1
	# 		self.iMarkRingStart %= len(self.markRing)

	# 	self.debugTraceMarkRing()

	# def addMarkToMarkRing(self):
	# 	if self.isMarkActive():
	# 		self.addToMarkRing(self.mark)

	# def addPrimaryCursorToMarkRing(self):
	# 	trace(LOG_MARK_RING, f"about to add primary cursor of {self.primaryCursor()}")
	# 	self.addToMarkRing(self.primaryCursor())

	# def markRingCt(self):
	# 	return self.getVirtualIndex(self.iMarkRingEnd) - self.iMarkRingStart

	# def isMarkRingEmpty(self):
	# 	# return self.iMarkRingStart == self.iMarkRingEnd

	# def isIMarkRingValid(self, iMarkRing):
	# 	# iOffset = self.getVirtualIndex(iMarkRing) - self.iMarkRingStart
	# 	# return (iOffset < self.markRingCt())

	# def getVirtualIndex(self, i):
	# 	if self.iMarkRingStart > i:
	# 		return i + len(self.markRing)

	# 	return i

	# def cycleMarkPrev(self, leaveMarkRingCrumbIfAtTail=True):

	# 	isAtTail = (self.getVirtualIndex(self.iMarkRingCycle) == self.iMarkRingEnd)

	# 	iPrev = self.iMarkRingCycle - 1
	# 	if iPrev < 0:
	# 		iPrev = len(self.markRing) - 1

	# 	if not self.isIMarkRingValid(iPrev):
	# 		trace(LOG_MARK_RING, f"cycle prev, i = {iPrev} is invalid")
	# 		return

	# 	# Keep looking back if these marks aren't actually moving us anywhere

	# 	markPrev = self.markRing[iPrev]
	# 	cursor = self.primaryCursor()

	# 	if cursor >= 0:

	# 		while cursor >= 0 and markPrev == cursor and iPrev != self.iMarkRingCycle:
	# 			trace(LOG_MARK_RING, f"cycle prev looping because mark i{iPrev} = {markPrev} and cursor = {cursor}")

	# 			iPrev -= 1
	# 			if iPrev < 0:
	# 				iPrev = len(self.markRing) - 1

	# 			if not self.isIMarkRingValid(iPrev):
	# 				trace(LOG_MARK_RING, f"cycle prev looping, i = {iPrev} is invalid")
	# 				return

	# 			markPrev = self.markRing[iPrev]
	# 			trace(LOG_MARK_RING, f"cycle prev looping, i = {iPrev}")


	# 	if markPrev >= 0:

	# 		# Maybe leave breadcrumb at the tail before jumping away

	# 		if leaveMarkRingCrumbIfAtTail and isAtTail and markPrev != self.primaryCursor():
	# 			self.addPrimaryCursorToMarkRing()

	# 		self.select(sublime.Region(markPrev, markPrev), MarkAction.CLEAR)
	# 		self.iMarkRingCycle = iPrev
	# 		if LOG_MARK_RING:
	# 			self.debugTraceMarkRing()
	# 			trace(LOG_MARK_RING, f"cycle prev to {markPrev}, i = {self.iMarkRingCycle}")
	# 	else:
	# 		raise AssertionError("Mark index is valid but value is <= 0?")

	# def cycleMarkNext(self):

	# 	iNext = self.iMarkRingCycle + 1
	# 	iNext %= len(self.markRing)

	# 	if not self.isIMarkRingValid(iNext):
	# 		trace(LOG_MARK_RING, f"cycle next, i = {iNext} is invalid")
	# 		return

	# 	markNext = self.markRing[iNext]
	# 	if markNext >= 0:
	# 		self.select(sublime.Region(markNext, markNext), MarkAction.CLEAR)
	# 		self.iMarkRingCycle = iNext
	# 		if LOG_MARK_RING:
	# 			self.debugTraceMarkRing()
	# 			trace(LOG_MARK_RING, f"cycle next to {markNext}, i = {self.iMarkRingCycle}")
	# 	else:
	# 		raise AssertionError("Mark index is valid but value is <= 0?")

	# def debugTraceMarkRing(self):
	# 	trace(LOG_MARK_RING, f"\nMark ring for view id = {self.view.id()}")
	# 	for i in range(len(self.markRing)):
	# 		endStr = ""
	# 		isCycleStr = ""
	# 		if i == self.iMarkRingCycle:
	# 			isCycleStr = "\t*"

	# 		if i == self.iMarkRingEnd and i == self.iMarkRingStart:
	# 			endStr = "\t[]"
	# 		elif i == self.iMarkRingStart:
	# 			endStr = "\tX"
	# 		elif i == self.iMarkRingEnd:
	# 			endStr = "\tO"
	# 		elif self.isIMarkRingValid(i):
	# 			endStr = "\t|"

	# 		trace(LOG_MARK_RING, f"{i}\t\t{str(self.markRing[i]).zfill(6)}{endStr}{isCycleStr}")


# --- Text Commands (Prefixed with 'Als' to distinguish between my commands and built-in sublime commands)

class AlsSetMark(sublime_plugin.TextCommand):

	def run(self, edit):
		markSel = MarkSel.get(self.view)

		if markSel.mark == markSel.primaryCursor():
			markSel.clearAll()
		else:
			markSel.placeMark(SelectionAction.CLEAR)

class AlsCycleMarkPrev(sublime_plugin.TextCommand):
	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.cycleMarkPrev()

class AlsCycleMarkNext(sublime_plugin.TextCommand):
	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.cycleMarkNext()

class AlsDebugTraceMarkRing(sublime_plugin.TextCommand):
	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.debugTraceMarkRing()

class AlsClearSelection(sublime_plugin.TextCommand):

	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.clearAll()

class AlsReverseSelection(sublime_plugin.TextCommand):

	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.select(MarkSel.reverseRegion(markSel.primaryRegion()), MarkAction.SET)

class AlsInflateSelectionToFillLines(sublime_plugin.TextCommand):

	"""Inflates the selection (and the mark) to the beginning/end of the lines at the beginning/end of the selection"""
	def run(self, edit):
		markSel = MarkSel.get(self.view)

		oldRegion = markSel.primaryRegion()
		newRegion = MarkSel.extendRegion(oldRegion, self.view.line(oldRegion))
		markSel.select(newRegion, MarkAction.SET)

def ensureTwoGroups(window):
	if window.num_groups() != 2:
		# HMM - This API is pretty shit, but I think it's the only thing we can do to force 2 groups?
		window.set_layout({
		    "cols": [0, 0.5, 1],
		    "rows": [0, 1],
		    "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
		})
		window.focus_group(0)	# predictable landing place if we end up having to modify layout!

class AlsOtherView(sublime_plugin.WindowCommand):
	def run(self):
		ensureTwoGroups(self.window)
		self.window.focus_group(1 if self.window.active_group() == 0 else 0)

class AlsTransposeViews(sublime_plugin.WindowCommand):
	def run(self):

		if self.window.num_groups() != 2:
			return

		activeViewBeforeTranspose = self.window.active_view()
		view0 = self.window.active_view_in_group(0)
		view1 = self.window.active_view_in_group(1)
		self.window.set_view_index(view0, 1, 0)
		self.window.set_view_index(view1, 0, 0)
		self.window.focus_view(activeViewBeforeTranspose)

class AlsCppOpenComplementaryFileInOppositeView(sublime_plugin.WindowCommand):

	def getComplementaryFilenameIfExists(self, filename, matchDefs):
		for ext, complements in matchDefs.items():
			if filename.endswith(ext):
				for complement in complements:
					candidate = filename[:-len(ext)] + complement
					if os.path.exists(candidate):
						return candidate

		return None

	def run(self):
		filename = self.window.active_view().file_name()

		# TODO - worth extending to .cxx, .cc, and other extensions that I don't really use (yet)?
		matchDefinitions = {
			'.h': ['.c', '.cpp'],
			'.hpp': ['.cpp', '.c'],
			'.c': ['.h', '.hpp'],
			'.cpp': ['.h', '.hpp']	# NOTE - I think .hpp extension is kinda dumb so I break the pattern and prioritize .h
		}

		complementaryFilename = self.getComplementaryFilenameIfExists(filename, matchDefinitions)
		if not complementaryFilename:
			return

		ensureTwoGroups(self.window)
		self.window.run_command("als_other_view")	# Move focus into the view that we will populate

		complementaryView = self.window.find_open_file(complementaryFilename)
		if complementaryView:
			# Populate with existing view
			self.window.set_view_index(complementaryView, self.window.active_group(), 0)
		else:
			# Populate by opening new file
			# TODO - verify that this succeeds? If getComplementaryFilenameIfExists does its job, it should...
			self.window.open_file(complementaryFilename)

class AlsSortViewsByFileType(sublime_plugin.WindowCommand):
	def run(self):
		ensureTwoGroups(self.window)	# HMM - Should this be an early out instead?

		leftExtensions = ['.h', '.hpp']
		rightExtensions = ['.c', '.cpp']

		# TODO
		# - Remember two current active views
		# - Move all left ext files to group 0
		# - Move all right ext files to group 1
		# - (Leave non-matching extensions untouched)
		# - (Prefer to leave previous two active views still active)
		# - (If they are both in the same group, then whichever one had focus wins out)

def findAndRunScript_inCurrentDirectory_orParent(activeView, command_name, script_name):
	fileName = activeView.file_name()
	if fileName:
		directory = Path(activeView.file_name())
		if not directory.is_dir():
			directory = directory.parent

		if not directory.is_dir():	raise AssertionError("Can't find directory to run script")

		scriptFile = None
		while True:
			scriptFile = directory / (f"{script_name}")
			if scriptFile.exists():
				break

			# HMM - Is this really the only/best way to check if we are at the root directory...? Sigh...
			parent = directory.parent
			if directory.samefile(parent):
				break

			directory = parent

		if scriptFile and scriptFile.exists():
			trace(LOG_BUILD, f"Running {str(scriptFile)}")
			out = subprocess.run([command_name, str(scriptFile)], capture_output=True, text=True).stdout
			print(out)
		else:
			trace(LOG_BUILD, f"ERROR: No {str(scriptFile)} found")

# def findAndRunPython_inCurrentOrParentDirectory(activeView, script_name):
# 	fileName = activeView.file_name()
# 	if fileName:
# 		directory = Path(activeView.file_name())
# 		if not directory.is_dir():
# 			directory = directory.parent

# 		if not directory.is_dir():	raise AssertionError("Can't find directory to run script")

# 		scriptFile = None
# 		while True:
# 			scriptFile = directory / (f"{script_name}.py")
# 			if scriptFile.exists():
# 				break

# 			# HMM - Is this really the only/best way to check if we are at the root directory...? Sigh...
# 			parent = directory.parent
# 			if directory.samefile(parent):
# 				break

# 			directory = parent

# 		if scriptFile and scriptFile.exists():
# 			trace(LOG_BUILD, f"Running {str(scriptFile)}")
# 			out = subprocess.run(["py", str(scriptFile)], capture_output=True, text=True).stdout
# 			print(out)
# 		else:
# 			trace(LOG_BUILD, f"ERROR: No {str(scriptFile)} found")

# def findAndRunPowershell_inCurrentOrParentDirectory(activeView, script_name):
# 	fileName = activeView.file_name()
# 	if fileName:
# 		directory = Path(activeView.file_name())
# 		if not directory.is_dir():
# 			directory = directory.parent

# 		if not directory.is_dir():	raise AssertionError("Can't find directory to run script")

# 		scriptFile = None
# 		while True:
# 			scriptFile = directory / (f"{script_name}.ps1")
# 			if scriptFile.exists():
# 				break

# 			# HMM - Is this really the only/best way to check if we are at the root directory...? Sigh...
# 			parent = directory.parent
# 			if directory.samefile(parent):
# 				break

# 			directory = parent

# 		if scriptFile and scriptFile.exists():
# 			trace(LOG_BUILD, f"Running {str(scriptFile)}")
# 			out = subprocess.run(["powershell.exe", str(scriptFile)], capture_output=True, text=True).stdout
# 			print(out)
# 		else:
# 			trace(LOG_BUILD, f"ERROR: No {str(scriptFile)} found")


class AlsBuildPy(sublime_plugin.WindowCommand):
	def run(self):
		print("\n" * 25)		# HACK
		self.window.run_command("save_all")		# HMM - Should we reduce this to only saving the files below the directory of the build script?
		findAndRunScript_inCurrentDirectory_orParent(
        	self.window.active_view(),
        	"python.exe",
        	"build.py")

class AlsRunPy(sublime_plugin.WindowCommand):
	def run(self):
		findAndRunScript_inCurrentDirectory_orParent(
	    	self.window.active_view(),
	    	"python.exe",
	    	"run.py")

class AlsBuildPowershell(sublime_plugin.WindowCommand):
	def run(self):
		self.window.run_command("hide_panel")
		self.window.run_command("save_all")		# HMM - Should we reduce this to only saving the files below the directory of the build script?
		findAndRunScript_inCurrentDirectory_orParent(
        	self.window.active_view(),
        	"powershell.exe",
        	"build.ps1")

class AlsRunPowershell(sublime_plugin.WindowCommand):
	def run(self):
		print("\n" * 25)		# HACK
		self.window.run_command("save_all")		# HMM - Should we reduce this to only saving the files below the directory of the build script?
		findAndRunScript_inCurrentDirectory_orParent(
        	self.window.active_view(),
        	"powershell.exe",
        	"run.ps1")

class AlsHidePanelThenRun(sublime_plugin.WindowCommand):
	"""Auto-close a panel and jump right back into the normal view with a command"""
	def run(self, **kwargs):

		# TODO - extend this command to run on panels that aren't i-search?
		#  Do we need to manually track which panel is open...? ugh...
		iSearch = ISearch.get(self.window)
		iSearch.treatCancelLikeDone = kwargs.get("call_on_done", False)

		command_name = kwargs["command_name"]
		command_args = kwargs["command_args"]

		trace(LOG_HIDE_PANEL_THEN_RUN, str(command_args))

		self.window.run_command("hide_panel")

		view = self.window.active_view()
		markSel = MarkSel.get(view)
		markSel.showSelection()		# NOTE - ISearch.onCancel doesn't get called until after our chained command runs (ugh...).
									#  Showing the selection NEEDS to run before we issue any move commands though!

		# NOTE - As of Sublime Text 4.0, calling run_command won't give your hooks a chance to intercept/modify it,
		#  which breaks the transient mark. So we manually call the hook before dispatching :)

		modified = None
		if AlsEventListener.instance:
			# HMM - It might be possible for this to run before AlsEventListener.__init__(..), but it seems very unlikely?
			#  This is just a safeguard, but the behavior might still be broken if that happens and something was depending on
			#  the on_text_command hook running
			modified = AlsEventListener.instance.on_text_command(view, command_name, command_args)

		if modified is None:
			trace(LOG_HIDE_PANEL_THEN_RUN, f"running {command_name} unmodified")
			view.run_command(command_name, command_args)
		else:
			lastModified = (command_name, command_args)
			while modified is not None:
				trace(LOG_HIDE_PANEL_THEN_RUN, f"modified {lastModified[0]} into {modified[0]}")
				lastModified = modified
				modified = AlsEventListener.instance.on_text_command(view, modified[0], modified[1])

			trace(LOG_HIDE_PANEL_THEN_RUN, f"running {lastModified[0]} (modified)")
			view.run_command(lastModified[0], lastModified[1])

# --- I-Search
#		https://www.gnu.org/software/emacs/manual/html_node/emacs/Repeat-Isearch.html
#		Features implemented from spec:
#			X search forward
#			x search backward
#			x highlight other matches
#			x repeat previous search
#			X case sensitivity determined by input		#	(default setting buried elsewhere in spec: https://www.gnu.org/software/emacs/manual/html_node/emacs/Special-Isearch.html)
#			* wrapped									#	Dropped. I prefer to auto-wrap without an extra search command.
#			_ overwrapped								#	HMM - Maybe show in status bar?
#			*  search ring								#	Dropped. I don't think I'd use this.

#		Custom features:
#			_ Show previous focus as a different tint once your search starts failing. Could we even show a red tint in the inputView by adding a region to it? That'd be fuckin metal.
#			_ Show outlines for matches in inactive views too? Have an easy hotkey to jump to next window?

#		Known bugs:
#		!!!	_ ctrl+s, esc, ctrl+s crashes sublime. (maybe any time you do a second i-search?)
#				commenting out onDeactivated fixes the issue. But I want that functionality :(
#			_ region only expands. if you search forward a couple times then going backwards should cause it to retract.

#		Fixed bugs
#			2021
#				6/15: move_to now properly exits the input panel when running i-search

class ISearch():

	# --- Constants

	PANEL_NAME = "i-search"
	FOUND_REGION_NAME = "als_find_highlight"
	FOCUS_REGION_NAME = "als_find_focus"
	EXTRA_SELECTION_REGION_NAME = "als_find_extra_selection"

	class Focus:

		class State(Enum):
			NIL		= 0		# No focus - search empty / not founcd
			PASSIVE	= 1		# ISearch eagerly focused on the user typed string, but they haven't otherwise interacted
			ACTIVE	= 2		# User interacted by searching for the next or prev match

		def __init__(self, state, region):
			self.state = state
			self.region = region

	NO_FOCUS = Focus(Focus.State.NIL, None)

	@staticmethod
	def get(window):
		trace(LOG_ISEARCH, "getting i-search mgr for window " + str(window.id()))
		return WindowEx.get(window).iSearch

	def __init__(self, window):
		self.window = window
		self.lastSavedSearch = ""
		self.cleanup(isAfterClose=False)

	def cleanup(self, isAfterClose):
		trace(LOG_ISEARCH, "cleanup")
		self.inputView = None
		self.cursorOnOpen = -1
		self.focus = ISearch.NO_FOCUS
		self.forward = True
		self.treatCancelLikeDone = False

		if isAfterClose:
			trace(LOG_ISEARCH, "isAfterClose")
			WindowEx.get(self.window).clearCustomStatus()

			activeView = self.window.active_view()
			markSel = MarkSel.get(activeView)

			markSel.showSelection()

			self.cleanupDrawings(activeView)

			if not markSel.isMarkActive():
				trace(LOG_ISEARCH, "clearing mark")
				markSel.clearAll()

	# --- Visibility

	def isShowing(self):
		return self.inputView and self.inputView.window()

	def open(self, forward=True, addPrimaryCursorToMarkRing=True):
		trace(LOG_ISEARCH, "open")

		markSel = MarkSel.get(self.window.active_view())
		if addPrimaryCursorToMarkRing:
			# markSel.addPrimaryCursorToMarkRing()
			pass

		self.cursorOnOpen = markSel.primaryCursor()
		self.forward = forward
		self.focus = ISearch.NO_FOCUS

		trace(LOG_ISEARCH, "check if showing")

		if not self.isShowing():
			trace(LOG_ISEARCH, "not already showing...")
			# NOTE - Not using self.lastSavedSearch as initial text. That requires the user manually re-trigger of search(..) with an empty search string
			self.inputView = self.window.show_input_panel(ISearch.PANEL_NAME, "", self.onDone, self.onChange, self.onCancel)
			self.inputViewEx = ViewEx.get(self.inputView)
			self.inputMarkSel = self.inputViewEx.markSel
		else:
			trace(LOG_ISEARCH, "already showing...")

		if not self.isShowing():
			trace(LOG_ISEARCH, "!!! we should be showing now but we are not")

		if not self.inputView:
			trace(LOG_ISEARCH, "!!! asserting")
			raise AssertionError("i-search has no input view?")

		trace(LOG_ISEARCH, "showing...")
		self.window.focus_view(self.inputView)

		windowEx = WindowEx.get(self.window)

		# self.inputView.run_command("select_all")	# HMM/TODO - This seems to somehow clear the text?.... which is actually what I want...

	def close(self):
		trace(LOG_ISEARCH, "close")
		if self.isShowing():
			if self.inputView.window():
				trace(LOG_ISEARCH, "(hide_panel)")
				self.inputView.window().run_command("hide_panel")
			else:
				trace(LOG_ISEARCH, "no window? not hiding...")

	def cleanupDrawings(self, view):
		view.erase_regions(ISearch.FOUND_REGION_NAME)
		view.erase_regions(ISearch.FOCUS_REGION_NAME)
		view.erase_regions(ISearch.EXTRA_SELECTION_REGION_NAME)

	# --- Hooks

	def onTextCommand(self, command_name, args):
		# HMM - I'd rather use (poorly documented) "chain" command here, but that doesn't let me hook into the chained commands :(
		#		Window seems to and Views seem not to but I've read on the internet that window sometimes messes up too... do I really
		#		need to write my own custom dispatcher?

		if command_name == "move" or command_name == "move_to":
			args["command_name"] = command_name
			args["call_on_done"] = True # HACK
			self.window.run_command("als_hide_panel_then_run", args)
			return ("", None)

		return None

	def onDeactivated(self):
		trace(LOG_ISEARCH, "on_deactivated")
		if self.isShowing():
			self.close()

	def onDone(self, text):
		trace(LOG_ISEARCH, "on_done")
		self.text = text # HMM - probably unnecessary?
		self.lastSavedSearch = text
		self.cleanup(isAfterClose=True)

	def onChange(self, text):
		self.text = text
		if self.text:
			self.search(isRepeatedSearch=False)
		else:
			pass	# TODO - Any state we want to clean up here, if they type stuff and then delete all the way back?

	def onCancel(self):
		trace(LOG_ISEARCH, "on_cancel")
		if self.treatCancelLikeDone:
			self.onDone(self.text)
		else:
			self.cleanup(isAfterClose=True)

	# --- Operations

	def search(
		self,
		isRepeatedSearch,
		edit=None):		# NOTE - required iff isRepeatedSearch

		trace(LOG_ISEARCH, f"BEGIN SEARCH FOR {self.text}")

		if isRepeatedSearch != (edit is not None):		raise AssertionError("Repeated searches require an 'edit' to behave properly in the case of an empty 'self.text'")

		# @HACK - If re-loading last saved search, we just set the text, and trust that the onChange hook will
		#	recurse back into search(..). For this reason, the function is re-entrant and returns after changing
		#	text (which triggers onChange)

		if not self.text:
			if self.lastSavedSearch and edit:
				self.inputView.replace(edit, self.inputViewEx.entireViewRegion(), self.lastSavedSearch)
				self.inputMarkSel.clearAll()
				self.inputView.run_command("move_to", { "to": "eof", "extend": False })
			else:
				return

		windowEx = WindowEx.get(self.window)
		markSel = MarkSel.get(self.window.active_view())
		keepMark = markSel.isMarkActive()
		markAction = MarkAction.KEEP if keepMark else MarkAction.CLEAR # @Redundant with keepMark

		# Clear multi-cursor
		primaryRegion = markSel.selectPrimaryRegion(markAction)

		# Decide where to start search
		searchFrom = None
		if self.forward:
			if self.focus.region:
				searchFrom = self.focus.region.end() - len(self.text)
			else:
				searchFrom = primaryRegion.end() - len(self.text)

			if isRepeatedSearch:
				searchFrom += 1
		else:
			if self.focus.region:
				searchFrom = self.focus.region.begin() + len(self.text)
			else:
				searchFrom = primaryRegion.begin() + len(self.text)

			if isRepeatedSearch:
				searchFrom -= 1

		flags = sublime.LITERAL
		hasNoUppercase = all(not c.isupper() for c in self.text)
		if hasNoUppercase:
			flags |= sublime.IGNORECASE

		# Find all matches
		# TODO - Cache matches if re-searching the same term and the buffer hasn't changed?
		#        Would be useful when continually re-searching to cycle through found selections

		activeView = self.window.active_view()
		found = activeView.find_all(self.text, flags=flags)

		if len(found) > 0:
			# Choose best match

			debug_matchPrev = sublime.Region(-1, -1)
			iBest = -1

			for i in range(len(found)):

				match = found[i]

				if match.b < match.a:
					raise AssertionError("find_all returned reversed region?")

				if match.a <= debug_matchPrev.a:
					raise AssertionError("find_all returned unsorted list?")

				if self.forward and match.a >= searchFrom:
					# NOTE - This one breaks the loop, since we can only get further away in an increasing list
					iBest = i
					break

				elif not self.forward and match.b <= searchFrom:
					# NOTE - This one doesn't, since we can only get closer and continue updating our ideal
					iBest = i

				debug_matchPrev = match

			wrappedAround = False
			if iBest == -1:
				if self.forward: 	iBest = 0				# wrap around to top match, HMM - require extra keypress to commit to wraparound?
				else:				iBest = len(found) -1	# ... to bot match ...
				wrappedAround = True

			# --- Lock in the match

			bestMatch = found[iBest]
			self.focus = ISearch.Focus(ISearch.Focus.State.ACTIVE if isRepeatedSearch else ISearch.Focus.State.PASSIVE,
										bestMatch)

			markSel.select(self.focus.region, markAction, extend=keepMark)
			markSel.hideSelection()

			windowEx.showCustomStatus(f"Match {iBest + 1} of {len(found)}")

			primaryRegion = markSel.primaryRegion()
			extraSelection = MarkSel.subtractRegion(primaryRegion, self.focus.region)

			# --- Draw around the matches!

			activeView.add_regions(
				ISearch.FOUND_REGION_NAME,
				found,
				# NOTE - Doesn't actually push the scope, just sources color from it. No way that I know of to actually push the "scope" :(
				scope=ISearch.FOUND_REGION_NAME,
				flags=sublime.DRAW_NO_FILL)

			activeView.add_regions(
				ISearch.FOCUS_REGION_NAME,
				[self.focus.region],
				scope=ISearch.FOCUS_REGION_NAME)

			activeView.add_regions(
				ISearch.EXTRA_SELECTION_REGION_NAME,
				extraSelection,
				scope=ISearch.EXTRA_SELECTION_REGION_NAME)

			if wrappedAround:
				if self.forward:	trace(LOG_ISEARCH, f"wraparound match found at ({match.a}, {match.b}) - ideal start: {searchFrom})")
				else:				trace(LOG_ISEARCH, f"wraparound match (r) found at ({match.a}, {match.b}) - ideal end: {searchFrom})")
			else:
				if self.forward:	trace(LOG_ISEARCH, f"match found at ({match.a}, {match.b}) - ideal start: {searchFrom})")
				else:				trace(LOG_ISEARCH, f"match (r) found at ({match.a}, {match.b}) - ideal end: {searchFrom})")
		else:
			# TODO - play beep here?
			windowEx.showCustomStatus(f"No matches")
			self.focus = ISearch.Focus(ISearch.Focus.State.NIL, None)
			self.cleanupDrawings(activeView)

			if self.forward:	trace(LOG_ISEARCH, "No match found")
			else: 				trace(LOG_ISEARCH, "No match (r) found")



class AlsIncrementalSearch(sublime_plugin.TextCommand):		# NOTE - TextCommand instead of WindowCommand
															#	TextCommand gives us access to the view, which lets us detect re-search
	def run(self, edit, forward=True):

		iSearch = ISearch.get(self.view.window())

		# --- Detect re-search
		# TODO
		if self.view.element() == InputPanel.ELEMENT_NAME:
			iSearch.forward = forward
			iSearch.search(isRepeatedSearch=True, edit=edit)
			return

		# --- Bail if inside some other special view
		if self.view.element():
			return

		# --- We're just inside a normal view
		iSearch.open(forward)


# --- Listeners/Hooks

class AlsEventListener(sublime_plugin.EventListener):

	instance = None

	def on_init(self, viewsCurrentlyLoaded):
		AlsEventListener.instance = self

	def on_text_command(self, view, command_name, args):

		# IMPORTANT - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things, otherwise there is inifnite recursion!

		trace(LOG_EVENTS, "text_command: " + command_name)

		# Maybe dispatch to input view

		if view.element() == InputPanel.ELEMENT_NAME:
			iSearch = ISearch.get(view.window())
			return iSearch.onTextCommand(command_name, args)

		# Extend moves

		markSel = MarkSel.get(view)
		if command_name == "move" or command_name == "move_to":
			if markSel.isMarkActive() and not args.get("extend", False):
				args["extend"] = True
				return (command_name, args)

		# NOTE - on_modified doesn't know what command caused the modification,
		#  We squirrel that info away so on_modified knows if it should drop the selection

		elif command_name == "swap_line_up" or \
			 command_name == "swap_line_down" or \
			 command_name == "indent" or \
			 command_name == "unindent":
			markSel.wantIgnoreModification += 1
			markSel.wantKeepMark += 1

		# Execute command unmodified

		return None

	def on_post_text_command(self, view, command_name, args):
		# NOTE - Commands that don't modify the buffer are handled here.
		#  All buffer-modifying commands are handled in on_modified

		trace(LOG_EVENTS, "on_post_text_command")

		if command_name == "copy":
			markSel = MarkSel.get(view)
			markSel.clearAll()

	def on_modified(self, view):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function

		trace(LOG_EVENTS, "on_modified")

		markSel = MarkSel.get(view)
		if markSel is None:
			return		# NOTE - filters out input view

		if markSel.wantIgnoreModification > 0:
			markSel.wantIgnoreModification -= 1
		elif markSel.isMarkActive():
			markSel.clearAll()

	def on_window_command(self, window, command_name, args):
		trace(LOG_EVENTS, "window_command: " + command_name)

		return None

	def on_activated(self, view):
		trace(LOG_EVENTS, "view activated: " + str(view.element()))

	def on_deactivated_async(self, view):
		# NOTE - must use async for this, otherwise sublime crashes
		# https://github.com/sublimehq/sublime_text/issues/5403

		# UGH - But this DOESN'T actually work because the view's window becomes None...
		#  we don't have timing guarantees since this is the async version...

		trace(LOG_EVENTS, "view deactivated (async): " + str(view.element()))

		# Maybe dispatch to input view

		if view.element() == InputPanel.ELEMENT_NAME:
			if view.window() == None:
				print("WTFFFFFFFFFFFFFFF")
			else:
				inputPanel = InputPanel.get(view.window())
				inputPanel.onDeactivated()

	def on_exit(self):
		pass

class AlsTestPanel1(sublime_plugin.WindowCommand):
	def run(self):
		print("t1")
		inputPanel = InputPanel.get(self.window)
		inputPanel.open("test 1")

class AlsTestPanel2(sublime_plugin.WindowCommand):
	def run(self):
		print("t2")
		inputPanel = InputPanel.get(self.window)
		inputPanel.open("test 2")

class InputPanel():

	LOG_TAG = None # or "input-panel"
	ELEMENT_NAME = "input:input"

	def __init__(self, window):
		self.window = window
		self.view = None
		self.name = None

	@staticmethod
	def get(window):
		trace(InputPanel.LOG_TAG, "getting i-search mgr for window " + str(window.id()))
		return WindowEx.get(window).inputPanel

	def onDeactivated(self):
		trace(InputPanel.LOG_TAG, "onDeactivated(..), name = " + str(self.name))
		self.close()

	def isShowing(self, name):
		return self.view and self.view.window() and self.name == name

	# TODO pass onDone, onChange, onCancel as params?
	# TODO what to do if we are already showing and we get this call?
	# Should we "re-open" and wire up new handlers?
	def open(self, name, onDone=None, onChange=None, onCancel=None):
		trace(InputPanel.LOG_TAG, "open(..), name = " + str(name))

		if self.isShowing(name):
			trace(InputPanel.LOG_TAG, "\tcalling focus_view(..)")
			self.window.focus_view(self.view)
		else:
			trace(InputPanel.LOG_TAG, "\tcalling show_input_panel(..)")
			self.view = self.window.show_input_panel(name, "", self.on_done, None, self.on_cancel)
			self.name = name
			trace(InputPanel.LOG_TAG, "\tself.view = " + str(self.view))

	def close(self):
		trace(InputPanel.LOG_TAG, "close(..), name = " + str(self.name))
		if self.isShowing(self.name):
			trace(InputPanel.LOG_TAG, '\tcalling run_command("hide_panel")')
			self.view.window().run_command("hide_panel")
