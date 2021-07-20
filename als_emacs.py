# TODO
# - Context sensitive status bar (e.g. match # 32/77)
# - change name from i-search to r-search when reverse-searching? (or i-search (r) ?)
# - Indent instead of inserting tab when I press tab/shift tab with a single line selected
# - If initial text matches something but then the text gets changed, start back from the beginning of the search
# - Make sure searches are highlighted in scroll bar (and maybe minimap?)
# - If active mark and then search forward a bunch of times and then backwards a bunch of times, shrink the selection back on the backwards searches
# - Ensure selecion is robust against wraparound search and wraparound reverse search
# - Better streamlined find/replace with yn.! options (see command mode? https://docs.sublimetext.io/reference/key_bindings.html#the-any-character-binding)
# - (Experiment) Drop mark after incremental search selection?
# - (Experiment) Consider always dropping a mark any time we have a selection? Why sholudn't shift insert a mark?
# - Mark ring
# - Jump focus to other sublime window (not just other view)
# - Consolidate all open tabs to 1 window. Option to close duplicates?
# - Force h on left and cpp on right?
# - virtual whitespace? maybe remap ctrl-alt-arrow to navigate

import sublime
import sublime_plugin

from enum import Enum
import traceback #debug

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



# --- Logging

LOG_TO_FILE = False

LOG_DEBUG = None
LOG_INIT = None
LOG_HIDE_PANEL_THEN_RUN = None
LOG_EVENTS = None
LOG_ISEARCH = None
LOG_BUILD = None
LOG_MARK_RING = None

LOG_DEBUG = "DEBUG"
# LOG_INIT = "init2"
# LOG_HIDE_PANEL_THEN_RUN = "als_hide_panel_then_run"
# LOG_EVENTS = "event listener"
# LOG_ISEARCH = "i-search"
# LOG_BUILD = "build"
LOG_MARK_RING = "mark-ring"




# --- Extra state stored per view window

class WindowEx():
	dictionary = {}

	def __init__(self, window):
		self.window = window
		self.iSearch = ISearch(window)

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
		self.window.active_view().set_status("als_custom", text)

	def clearCustomStatus(self):
		# HMM - Should we set it for all views? Weird that this is something we set per-view, despite being displayed per-window
		self.window.active_view().erase_status("als_custom")


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
	# 	alsTrace(True, f"Adding {iPos} to mark ring (i = {self.iMarkRingEnd}, view element = {self.view.element()}, view id = {self.view.id()}")

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
	# 				alsTrace(True, f"Skipping duplicate addToMarkRing with iPos = {iPos} (same as mark i{iPrev})")
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
	# 	alsTrace(LOG_MARK_RING, f"about to add primary cursor of {self.primaryCursor()}")
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
	# 		alsTrace(LOG_MARK_RING, f"cycle prev, i = {iPrev} is invalid")
	# 		return

	# 	# Keep looking back if these marks aren't actually moving us anywhere

	# 	markPrev = self.markRing[iPrev]
	# 	cursor = self.primaryCursor()

	# 	if cursor >= 0:

	# 		while cursor >= 0 and markPrev == cursor and iPrev != self.iMarkRingCycle:
	# 			alsTrace(LOG_MARK_RING, f"cycle prev looping because mark i{iPrev} = {markPrev} and cursor = {cursor}")

	# 			iPrev -= 1
	# 			if iPrev < 0:
	# 				iPrev = len(self.markRing) - 1

	# 			if not self.isIMarkRingValid(iPrev):
	# 				alsTrace(LOG_MARK_RING, f"cycle prev looping, i = {iPrev} is invalid")
	# 				return

	# 			markPrev = self.markRing[iPrev]
	# 			alsTrace(LOG_MARK_RING, f"cycle prev looping, i = {iPrev}")


	# 	if markPrev >= 0:

	# 		# Maybe leave breadcrumb at the tail before jumping away

	# 		if leaveMarkRingCrumbIfAtTail and isAtTail and markPrev != self.primaryCursor():
	# 			self.addPrimaryCursorToMarkRing()

	# 		self.select(sublime.Region(markPrev, markPrev), MarkAction.CLEAR)
	# 		self.iMarkRingCycle = iPrev
	# 		if LOG_MARK_RING:
	# 			self.debugTraceMarkRing()
	# 			alsTrace(LOG_MARK_RING, f"cycle prev to {markPrev}, i = {self.iMarkRingCycle}")
	# 	else:
	# 		raise AssertionError("Mark index is valid but value is <= 0?")

	# def cycleMarkNext(self):

	# 	iNext = self.iMarkRingCycle + 1
	# 	iNext %= len(self.markRing)

	# 	if not self.isIMarkRingValid(iNext):
	# 		alsTrace(LOG_MARK_RING, f"cycle next, i = {iNext} is invalid")
	# 		return

	# 	markNext = self.markRing[iNext]
	# 	if markNext >= 0:
	# 		self.select(sublime.Region(markNext, markNext), MarkAction.CLEAR)
	# 		self.iMarkRingCycle = iNext
	# 		if LOG_MARK_RING:
	# 			self.debugTraceMarkRing()
	# 			alsTrace(LOG_MARK_RING, f"cycle next to {markNext}, i = {self.iMarkRingCycle}")
	# 	else:
	# 		raise AssertionError("Mark index is valid but value is <= 0?")

	# def debugTraceMarkRing(self):
	# 	alsTrace(LOG_MARK_RING, f"\nMark ring for view id = {self.view.id()}")
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

	# 		alsTrace(LOG_MARK_RING, f"{i}\t\t{str(self.markRing[i]).zfill(6)}{endStr}{isCycleStr}")


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

class AlsOtherView(sublime_plugin.WindowCommand):
	def run(self):
		if self.window.num_groups() != 2:
			# HMM - This API is pretty shit, but I think it's the only thing we can do to force 2 groups?
			self.window.set_layout({
			    "cols": [0, 0.5, 1],
			    "rows": [0, 1],
			    "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
			})
			self.window.focus_group(1)
		else:
			iGroupActive = self.window.active_group()
			iGroupActiveDesired = (iGroupActive + 1) % self.window.num_groups()
			self.window.focus_group(iGroupActiveDesired)

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

from pathlib import Path
import os

class AlsRunBuildBat(sublime_plugin.WindowCommand):
	def run(self):
		print("RTS_ROOT: " + os.environ['RTS_ROOT'])

		activeView = self.window.active_view()
		fileName = activeView.file_name()
		if fileName:
			directory = Path(activeView.file_name())
			if not directory.is_dir():
				directory = directory.parent

			if not directory.is_dir():	raise AssertionError("Can't find directory to run build file")

			buildFile = None
			while True:
				buildFile = directory / "build.bat"
				if buildFile.exists():
					break

				# HMM - Is this really the only/best way to check if we are at the root directory...? Sigh...
				parent = directory.parent
				if directory.samefile(parent):
					break

				directory = parent

			if buildFile and buildFile.exists():
				alsTrace(LOG_BUILD, f"Running {str(buildFile)}")
				self.window.run_command("exec", { "shell_cmd": str(buildFile)})
			else:
				alsTrace(LOG_BUILD, "No build.bat found")

class AlsHidePanelThenRun(sublime_plugin.WindowCommand):
	"""Auto-close a panel and jump right back into the normal view with a command"""
	def run(self, **kwargs):

		iSearch = ISearch.get(self.window)
		command_name = kwargs.pop('command_name')

		alsTrace(LOG_HIDE_PANEL_THEN_RUN, str(kwargs))

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
			modified = AlsEventListener.instance.on_text_command(view, command_name, kwargs)


		if modified is None:
			alsTrace(LOG_HIDE_PANEL_THEN_RUN, f"running {command_name} unmodified")
			view.run_command(command_name, kwargs)
		else:
			lastModified = (command_name, kwargs)
			while modified is not None:
				alsTrace(LOG_HIDE_PANEL_THEN_RUN, f"modified {lastModified[0]} into {modified[0]}")
				lastModified = modified
				modified = AlsEventListener.instance.on_text_command(view, modified[0], modified[1])

			alsTrace(LOG_HIDE_PANEL_THEN_RUN, f"running {lastModified[0]} (modified)")
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

	INPUT_ELEMENT = "input:input"
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
		return WindowEx.get(window).iSearch

	def __init__(self, window):
		self.window = window
		self.lastSavedSearch = ""
		self.cleanup(isAfterClose=False)

	def cleanup(self, isAfterClose):
		self.inputView = None
		self.cursorOnOpen = -1
		self.focus = ISearch.NO_FOCUS
		self.forward = True

		if isAfterClose:
			WindowEx.get(self.window).clearCustomStatus()

			activeView = self.window.active_view()
			markSel = MarkSel.get(activeView)

			markSel.showSelection()

			self.cleanupDrawings(activeView)

			if not markSel.isMarkActive():
				markSel.clearAll()

	# --- Visibility

	def isShowing(self):
		return self.inputView and self.inputView.window()

	def open(self, forward=True, addPrimaryCursorToMarkRing=True):

		markSel = MarkSel.get(self.window.active_view())
		if addPrimaryCursorToMarkRing:
			# markSel.addPrimaryCursorToMarkRing()
			pass

		self.cursorOnOpen = markSel.primaryCursor()
		self.forward = forward
		self.focus = ISearch.NO_FOCUS

		if not self.isShowing():
			# NOTE - Not using self.lastSavedSearch as initial text. That requires a manual re-trigger of search(..) with an empty search string
			self.inputView = self.window.show_input_panel(ISearch.PANEL_NAME, "", self.onDone, self.onChange, self.onCancel)
			self.inputViewEx = ViewEx.get(self.inputView)
			self.inputMarkSel = self.inputViewEx.markSel

		if not self.inputView:
			raise AssertionError("i-search has no input view?")

		self.window.focus_view(self.inputView)
		# self.inputView.run_command("select_all")	# HMM/TODO - This seems to somehow clear the text?.... which is actually what I want...

	def close(self):
		if self.isShowing():
			self.window.run_command("hide_panel")

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
			self.window.run_command("als_hide_panel_then_run", args)
			return ("", None)

		return None

	def onDeactivated(self):
		self.close()

	def onDone(self, text):
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
		self.cleanup(isAfterClose=True)

	# --- Operations

	def search(
		self,
		isRepeatedSearch,
		edit=None):		# NOTE - required iff isRepeatedSearch

		alsTrace(LOG_ISEARCH, f"BEGIN SEARCH FOR {self.text}")

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
				if self.forward:	alsTrace(LOG_ISEARCH, f"wraparound match found at ({match.a}, {match.b}) - ideal start: {searchFrom})")
				else:				alsTrace(LOG_ISEARCH, f"wraparound match (r) found at ({match.a}, {match.b}) - ideal end: {searchFrom})")
			else:
				if self.forward:	alsTrace(LOG_ISEARCH, f"match found at ({match.a}, {match.b}) - ideal start: {searchFrom})")
				else:				alsTrace(LOG_ISEARCH, f"match (r) found at ({match.a}, {match.b}) - ideal end: {searchFrom})")
		else:
			# TODO - play beep here?
			windowEx.showCustomStatus(f"No matches")
			self.focus = ISearch.Focus(ISearch.Focus.State.NIL, None)
			self.cleanupDrawings(activeView)

			if self.forward:	alsTrace(LOG_ISEARCH, "No match found")
			else: 				alsTrace(LOG_ISEARCH, "No match (r) found")



class AlsIncrementalSearch(sublime_plugin.TextCommand):		# NOTE - TextCommand instead of WindowCommand
															#	TextCommand gives us access to the view, which lets us detect re-search
	def run(self, edit, forward=True):

		iSearch = ISearch.get(self.view.window())

		# --- Detect re-search
		if self.view.element() == ISearch.INPUT_ELEMENT:
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

		alsTrace(LOG_EVENTS, "[event listener] text_command: " + command_name)

		# Maybe dispatch to input view

		if view.element() == ISearch.INPUT_ELEMENT:
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

		alsTrace(LOG_EVENTS, "[event listener] on_post_text_command")

		if command_name == "copy":
			markSel = MarkSel.get(view)
			markSel.clearAll()

	def on_modified(self, view):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function

		alsTrace(LOG_EVENTS, "[event listener] on_modified")

		markSel = MarkSel.get(view)
		if markSel is None:
			return		# NOTE - filters out input view

		if markSel.wantIgnoreModification > 0:
			markSel.wantIgnoreModification -= 1
		elif markSel.isMarkActive():
			markSel.clearAll()

	def on_window_command(self, window, command_name, args):
		alsTrace(LOG_EVENTS, "[event listener] window_command: " + command_name)

		return None

	def on_activated(self, view):
		alsTrace(LOG_EVENTS, "view activated: " + str(view.element()))

	# FIXME - If uncommented, always crashing the second search!!!!!
	# def on_deactivated(self, view):
	# 	alsTrace(LOG_EVENTS, "view deactivated: " + str(view.element()))

	# 	# Maybe dispatch to input view

	# 	if view.element() == ISearch.INPUT_ELEMENT:
	# 		iSearch = ISearch.get(view.window())
	# 		return iSearch.onDeactivated()

	def on_exit(self):
		pass

def plugin_loaded():
	if LOG_TO_FILE:
		with open('als_trace.txt','w') as file:
		    pass	# NOTE - Clears file

# TODO - alsAssert which logs + exits on failure, instead of raising AssertionError
def alsTrace(tag, line):
	if tag:
		if LOG_TO_FILE:
			# SLOW - opening file every time we log lol
			with open('als_trace.txt', 'a') as file:
				file.write(f"[{tag}] {line}\n")
		else:
			print(line)
