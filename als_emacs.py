# TODO
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

import sublime
import sublime_plugin

from enum import Enum
import traceback #debug

# --- Extra state stored per view and per window

class ViewEx():
	dictionary = {}

	def __init__(self, view):
		self.markSel = MarkSel(view)
		# self.iSearch = ISearch(view, self.markSel)

	@staticmethod
	def get(view):
		result = ViewEx.dictionary.get(view.id())

		if result is None:
			result = ViewEx(view)
			ViewEx.dictionary[view.id()] = result

		return result

	@staticmethod
	def onClose(view):
		ViewEx.dictionary.pop(view.id())

class WindowEx():
	dictionary = {}

	def __init__(self, window):
		self.iSearch = ISearch(window)

	@staticmethod
	def get(window):
		result = WindowEx.dictionary.get(window.id())

		if result is None:
			result = WindowEx(window)
			WindowEx.dictionary[window.id()] = result

		return result

	@staticmethod
	def onClose(window):
		WindowEx.dictionary.pop(window.id())


# --- Mark/selection (similar to 'transient-mark-mode' in emacs)

class SelectionAction(Enum):
	CLEAR 					= 0
	KEEP 					= 1

class MarkAction(Enum):
	CLEAR					= 0
	KEEP					= 1 # NOTE - SETs if already active, CLEARs if otherwise
	SET						= 2

class MarkSel():

	@staticmethod
	def get(view):
		return ViewEx.get(view).markSel

	def __init__(self, view):
		self.view = view
		self.selection = view.sel()
		self.hiddenSelRegion = None			# Supports ISearch focus color not being obscured by a selected region
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
	def placeMark(self, selectionAction):
		cursor = self.primaryCursor()
		if self.mark != cursor:
			self.mark = self.primaryCursor()

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

		elif 	(markAction == MarkAction.SET) or \
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



# --- Text Commands (Prefixed with 'Als' to distinguish between my commands and built-in sublime commands)

class AlsSetMark(sublime_plugin.TextCommand):

	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.placeMark(SelectionAction.CLEAR)

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
		# newRegion = MarkSel.extendRegion(oldRegion, self.view.line(oldRegion))
		markSel.select(newRegion, MarkAction.SET)

class AlsHidePanelThenRun(sublime_plugin.WindowCommand):
	"""Auto-close a panel and jump right back into the normal view with a command"""
	def run(self, **kwargs):
		iSearch = ISearch.get(self.window)

		self.window.run_command("hide_panel")

		view = self.window.active_view()
		markSel = MarkSel.get(view)
		markSel.showSelection()		# NOTE - ISearch.onCancel doesn't get called until after our chained command runs (ugh...).
									#  Showing the selection NEEDS to run before we issue any move commands though!

		# NOTE - As of Sublime Text 4.0, calling run_command won't give your hooks a chance to intercept/modify it,
		#  which breaks the transient mark. So we manually call the hook before dispatching :)

		command_name = kwargs.pop('command_name')

		modified = None
		if AlsEventListener.instance:
			# HMM - It might be possible for this to run before AlsEventListener.__init__(..), but it seems very unlikely?
			#  This is just a safeguard, but the behavior might still be broken if that happens and something was depending on
			#  the on_text_command hook running
			modified = AlsEventListener.instance.on_text_command(view, command_name, kwargs)


		if modified is None:
			print(f"running {command_name} unmodified")
			view.run_command(command_name, kwargs)
		else:
			lastModified = (command_name, kwargs)
			while modified is not None:
				print(f"modified {lastModified[0]} into {modified[0]}")
				lastModified = modified
				modified = AlsEventListener.instance.on_text_command(view, modified[0], modified[1])

			print(f"running {lastModified[0]} (modified)")
			view.run_command(lastModified[0], lastModified[1])

# --- I-Search

# TODO
# ‘Overwrapped’ search, which means that you are revisiting matches that you have already seen (i.e., starting from cursorOnOpen)


class ISearch():

	# --- Constants

	INPUT_ELEMENT = "input:input"
	PANEL_NAME = "i-search"
	FOUND_REGION_NAME = "als_find_highlight"
	FOCUS_REGION_NAME = "als_find_focus"
	EXTRA_SELECTION_REGION_NAME = "als_find_extra_selection"
	DEBUG_LOG = False

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
			activeView = self.window.active_view()
			markSel = MarkSel.get(activeView)
			markSel.showSelection()
			self.cleanupDrawings(activeView)
			if not markSel.isMarkActive():
				markSel.clearAll()

	# --- Visibility

	def isShowing(self):
		return bool(self.inputView and self.inputView.window())

	def open(self, forward=True):
		markSel = MarkSel.get(self.window.active_view())
		self.cursorOnOpen = markSel.primaryCursor()
		self.forward = forward
		self.focus = ISearch.NO_FOCUS

		if not self.isShowing():
			self.inputView = self.window.show_input_panel(ISearch.PANEL_NAME, self.lastSavedSearch, self.onDone, self.onChange, self.onCancel)

		if not self.inputView:		raise AssertionError("i-search has no input view?")

		self.window.focus_view(self.inputView)
		self.inputView.run_command("select_all")

	def close(self):
		if self.isShowingInputView():
			self.window.run_command("hide_panel")
			self.cleanup(isAfterClose=True)

	def cleanupDrawings(self, view):
		view.erase_regions(ISearch.FOUND_REGION_NAME)
		view.erase_regions(ISearch.FOCUS_REGION_NAME)
		view.erase_regions(ISearch.EXTRA_SELECTION_REGION_NAME)

	# --- Hooks

	def onTextCommand(self, command_name, args):
		# HMM - I'd rather use (poorly documented) "chain" command here, but that doesn't let me hook into the chained commands :(

		if command_name == "move" or command_name == "move_to":
			args["command_name"] = command_name
			self.inputView.run_command("als_hide_panel_then_run", args)
			return ("", None)

		return None

	def onDone(self, text):
		print("INPUT DONE")
		self.text = text # HMM - probably unnecessary?
		self.lastSavedSearch = text
		self.cleanup(isAfterClose=True)

	def onChange(self, text):
		self.text = text
		self.search(isRepeatedSearch=False)

	def onCancel(self):
		self.cleanup(isAfterClose=True)

	# --- Operations

	def search(self, isRepeatedSearch):

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
			bestMatch = None

			for match in found:

				if match.b < match.a:
					raise AssertionError("find_all returned reversed region?")

				if match.a <= debug_matchPrev.a:
					raise AssertionError("find_all returned unsorted list?")

				if self.forward and match.a >= searchFrom:
					# NOTE - This one breaks the loop, since we can only get further away in an increasing list
					bestMatch = match
					break

				elif not self.forward and match.b <= searchFrom:
					# NOTE - This one doesn't, since we can only get closer and continue updating our ideal
					bestMatch = match

				debug_matchPrev = match

			wrappedAround = False
			if not bestMatch:
				if self.forward: 	bestMatch = found[0]	# wrap around to top match, HMM - require extra keypress to commit to wraparound?
				else:				bestMatch = found[-1]	# ... to bot match ...
				wrappedAround = True

			# --- Lock in the match

			self.focus = ISearch.Focus(ISearch.Focus.State.ACTIVE if isRepeatedSearch else ISearch.Focus.State.PASSIVE,
										bestMatch)

			markSel.select(self.focus.region, markAction, extend=keepMark)
			markSel.hideSelection()

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

			if ISearch.DEBUG_LOG:
				if wrappedAround:
					if self.forward:	print(f"wraparound match found at ({match.a}, {match.b}) - ideal start: {searchFrom})")
					else:				print(f"wraparound match (r) found at ({match.a}, {match.b}) - ideal end: {searchFrom})")
				else:
					if self.forward:	print(f"match found at ({match.a}, {match.b}) - ideal start: {searchFrom})")
					else:				print(f"match (r) found at ({match.a}, {match.b}) - ideal end: {searchFrom})")
		else:
			# TODO - play beep here?
			self.focus = ISearch.Focus(ISearch.Focus.State.NIL, None)
			self.cleanupDrawings(activeView)
			if ISearch.DEBUG_LOG:
				if self.forward:	print("No match found")
				else: 				print("No match (r) found")



class AlsIncrementalSearch(sublime_plugin.TextCommand):		# NOTE - TextCommand instead of WindowCommand
															#	TextCommand gives us access to the view, which lets us detect re-search

	def run(self, edit, forward=True):

		iSearch = ISearch.get(self.view.window())

		# --- Detect re-search
		if self.view.element() == ISearch.INPUT_ELEMENT:
			iSearch.forward = forward
			iSearch.search(isRepeatedSearch=True)
			return

		# --- Bail if inside some other special view
		if self.view.element():
			return

		# --- We're just inside a normal view
		iSearch.open(forward)


# --- Listeners/Hooks

class AlsEventListener(sublime_plugin.EventListener):
	DEBUG_LOG = False

	instance = None

	def on_init(self, viewsCurrentlyLoaded):
		AlsEventListener.instance = self

	def on_text_command(self, view, command_name, args):

		# IMPORTANT - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things, otherwise there is inifnite recursion!

		if self.DEBUG_LOG:	print("[event listener] text_command: " + command_name)

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

		if self.DEBUG_LOG:	print("[event listener] on_post_text_command")

		if command_name == "copy":
			markSel = MarkSel.get(view)
			markSel.clearAll()

	def on_modified(self, view):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function

		if self.DEBUG_LOG:	print("[event listener] on_modified")

		markSel = MarkSel.get(view)
		if markSel is None:
			return		# NOTE - filters out input view

		if markSel.wantIgnoreModification > 0:
			markSel.wantIgnoreModification -= 1
		elif markSel.isMarkActive():
			markSel.clearAll()

	def on_window_command(self, window, command_name, args):
		if self.DEBUG_LOG: print("[event listener] window_command: " + command_name)

		return None

	def on_activated(self, view):
		if self.DEBUG_LOG: print("view activated: " + str(view.element()))

	def on_deactivated(self, view):
		if self.DEBUG_LOG: print("view deactivated: " + str(view.element()))
