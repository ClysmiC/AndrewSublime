# TODO
# - If initial text matches something but then the text gets changed, start back from the beginning of the search
# - If active mark and then search forward a bunch of times and then backwards a bunch of times, shrink the selection back on the backwards searches
# - Better streamlined find/replace with yn.! options (see command mode? https://docs.sublimetext.io/reference/key_bindings.html#the-any-character-binding)
# - (Experiment) Drop mark after incremental search selection?
# - Mark ring
# - Jump focus to other sublime window (not just other view)
# - Consolidate all open tabs to 1 window. Option to close duplicates?
# - Force h on left and cpp on right?

import sublime
import sublime_plugin

from enum import Enum
import traceback #debug

# --- Extra state stored per view to enable behavior similar to emacs 'trasient-mark-mode'

g_viewExDict = {}

class ViewEx():
	def __init__(self, view):
		self.markSel = MarkSel(view)
		self.iSearch = ISearch(view, self.markSel)

	@staticmethod
	def get(view):
		result = g_viewExDict.get(view.id())

		if result is None:
			result = ViewEx(view)
			g_viewExDict[view.id()] = result

		return result

	@staticmethod
	def getActiveView(window):
		activeView = window.active_view()
		if activeView is None:		raise AssertionError("No active view?")
		if activeView.element():	raise AssertionError("Active view has a truthy element(..) value?")
		return activeView

	# TODO - Delete from dict if view goes away?

class SelectionAction(Enum):
	CLEAR 					= 0
	KEEP 					= 1

class MarkAction(Enum):
	CLEAR					= 0
	SET						= 1

# --- Mark/selection

class MarkSel():

	FALLBACK_REGION = sublime.Region(0, 0)

	@staticmethod
	def get(view):
		return ViewEx.get(view).markSel

	def __init__(self, view):
		self.view = view
		self.selection = view.sel()
		self.clearMark()

	def clearMark(self):
		self.mark = -1
		self.cntModificationToIgnore = 0 	# State for re-jiggering selection during various commands
		self.cntWantRefreshMark = 0			# ...

	# ---

	@staticmethod
	def ensureRegionValid(region):
		if region is None or region.a < 0 or region.b < 0: # HMM - check upper bounds? That'd require view info...
			return sublime.Region(0, 0)

		return region

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

	def primaryCursor(self):
		return self.selection[0].b if len(self.selection) > 0 else 0

	def primaryRegion(self):
		return MarkSel.ensureRegionValid(self.selection[0] if len(self.selection) > 0 else FALLBACK_REGION) # @Slow - Redundant checks

	def isMarkActive(self):
		return self.mark >= 0 and len(self.selection) == 1

	def placeMark(self, selectionAction):
		self.mark = self.primaryCursor()

		if selectionAction == SelectionAction.CLEAR:
			self.selection.clear()
			self.selection.add(sublime.Region(self.mark, self.mark))

		elif selectionAction == SelectionAction.KEEP:
			pass

	def select(self, region, markAction, asExtension=False, wantShow=True):

		if asExtension:
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

		elif markAction == MarkAction.SET:
			self.mark = region.a

		return region

	def selectPrimaryRegion(self, markAction, asExtension=False, wantShow=True):
		return self.select(self.primaryRegion(), markAction, asExtension=asExtension, wantShow=wantShow)

	def clearAll(self):
		cursor = self.primaryCursor()
		self.select(sublime.Region(cursor, cursor), MarkAction.CLEAR)

	def isSingleNonEmptySelection(self):
		return len(self.selection) == 1 and self.selection[0].a != self.selection[0].b

	def isSingleEmptySelection(self):
		return len(self.selection) == 1 and self.selection[0].a == self.selection[0].b



# --- Text Commands (Prefixed with 'Als' to distinguish between my commands and built-in sublime commands)

class AlsSetMark(sublime_plugin.TextCommand):
	"""Sets the mark"""
	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.placeMark(SelectionAction.CLEAR)

class AlsClearSelection(sublime_plugin.TextCommand):

	"""Clears the selection (and the mark)"""
	def run(self, edit):
		markSel = MarkSel.get(self.view)
		markSel.clearAll()

class AlsInflateSelectionToFillLines(sublime_plugin.TextCommand):

	"""Inflates the selection (and the mark) to the beginning/end of the lines at the beginning/end of the selection"""
	def run(self, edit):
		markSel = MarkSel.get(self.view)

		newRegion = MarkSel.extendRegion(markSel.primaryRegion(), self.view.line(primaryRegion))
		markSel.select(newRegion, MarkAction.SET)

class AlsHidePanelThenRun(sublime_plugin.TextCommand):
	"""Auto-close a panel and jump right back into the normal view with a command"""
	def run(self, edit, **kwargs):
		window = self.view.window()
		window.run_command("hide_panel")

		view = ViewEx.getActiveView(window)
		viewEx = ViewEx.get(view)
		viewEx.markSel.clearAll()

		command_name = kwargs.pop('command_name', None)
		view.run_command(command_name, kwargs)

# --- I-Search

# TODO
# ‘Overwrapped’ search, which means that you are revisiting matches that you have already seen (i.e., starting from cursorOnOpen)

class ISearch():
	INPUT_ELEMENT = "input:input"
	PANEL_NAME = "i-search"
	FOUND_REGION_NAME = "als_i_search_found"
	FOCUS_REGION_NAME = "als_i_search_focus"
	NO_FOCUS = sublime.Region(-1, -1)

	@staticmethod
	def get(view):
		return ViewEx.get(view).iSearch

	def __init__(self, view, markSel):
		# --- Persistent state
		LOG = False
		if LOG:	print("Initializing view " + str(view))

		self.view = view
		self.markSel = markSel
		self.lastCommittedText = ""

		# --- Temp state
		self.cleanupTempState()

	def cleanupTempState(self):
		self.cursorOnOpen = -1
		self.focus = None
		self.text = ""
		self.inputView = None
		self.autoPreferForward = True	# Smuggles in the correct forward value for the initial onChange(..) after show_input_panel(..)
		self.cleanupDrawings()

	def ensureOpen(self, forward):
		self.autoPreferForward = forward

		# HMM - Worried about this falling out of sync by missing an edge where we should cleanup(..)
		#  ... but more woried about uncertainty of calling show_input_panel while it is already visible
		if self.inputView is None:
			textInitial = self.lastCommittedText if self.lastCommittedText else ""
			self.inputView = self.view.window().show_input_panel(self.PANEL_NAME, textInitial, self.onDone, self.onChange, self.onCancel)
			self.inputView.run_command("select_all")

	def forceClose(self):
		self.window.run_command("hide_panel")
		self.cleanupTempState()

	def cleanupDrawings(self):
		self.view.erase_regions(self.FOUND_REGION_NAME)
		self.view.erase_regions(self.FOCUS_REGION_NAME)

	# --- Hooks

	@staticmethod
	def on_text_command(viewInput, command_name, args):
		# NOTE - Hooked into by EventListener
		if viewInput.element() != ISearch.INPUT_ELEMENT:	raise AssertionError(f"Expected viewInput to be {ISearch.INPUT_ELEMENT}")

		if command_name == "move" or command_name == "move_to":
			args["command_name"] = command_name
			viewInput.run_command("als_hide_panel_then_run", args)
			return ("", None)

		return None

	def onDone(self, text):
		self.lastCommittedText = text
		self.cleanupTempState()
		if not self.markSel.isMarkActive():
			self.markSel.clearAll()

	def onChange(self, text):
		if len(self.markSel.selection) != 1:
			self.forceClose()
			return

		self.text = text
		self.search(self.autoPreferForward)

	def onCancel(self):
		self.cleanupTempState()
		if not self.markSel.isMarkActive():
			self.markSel.clearAll()

	# --- Operations

	def search(self, forward, nudge=False):
		LOG = True

		self.autoPreferForward = forward

		if not self.text:
			return

		isMarkActive = self.markSel.isMarkActive()
		markAction = MarkAction.SET if isMarkActive else MarkAction.CLEAR

		primaryRegion = self.markSel.selectPrimaryRegion(markAction)

		startFrom = self.focus
		if startFrom is None:
			startFrom = primaryRegion

		idealFocusBound = None
		if forward:
			idealFocusBound = startFrom.begin() + (1 if nudge else 0)
		else:
			idealFocusBound = startFrom.end() - (1 if nudge else 0)

		flags = sublime.LITERAL
		hasAnyUppercase = any(c.isupper() for c in self.text)
		if not hasAnyUppercase:
			flags |= sublime.IGNORECASE

		found = self.view.find_all(self.text, flags=flags)

		if len(found) > 0:
			# Compute ideal match

			debug_matchPrev = sublime.Region(-1, -1)
			bestMatch = None

			for match in found:

				if match.b < match.a:
					raise AssertionError("find_all returned reversed region?")

				if match.a <= debug_matchPrev.a:
					raise AssertionError("find_all returned unsorted list?")

				if forward and match.a >= idealFocusBound:
					# NOTE - This one breaks the loop, since we can only get further away in an increasing list
					bestMatch = match
					break

				elif not forward and match.b <= idealFocusBound:
					# NOTE - This one doesn't, since we can only get closer and continue updating our ideal
					bestMatch = match

				debug_matchPrev = match

			wrappedAround = False
			if not bestMatch:
				if forward: bestMatch = found[0]	# wrap around to top match, HMM - require extra keypress?
				else:		bestMatch = found[-1]	# ... to bot match ...
				wrappedAround = True

			# --- Lock in the match

			self.focus = bestMatch

			# HMM - asExtension check is kinda roundabout. Basically I want to extend any time we have a mark down
			self.markSel.select(self.focus, markAction, asExtension=isMarkActive)

			# TODO - Make color match theme instead of hard-coding

			# --- Found
			self.view.add_regions(
				self.FOUND_REGION_NAME,
				found,
				scope="region.orangish",
				flags=sublime.DRAW_NO_FILL)

			# --- Focus
			self.view.add_regions(
				self.FOCUS_REGION_NAME,
				[self.focus],
				scope="invalid")

			if LOG:
				if wrappedAround:
					if forward:	print(f"wraparound match found at ({match.a}, {match.b}) - ideal start: {idealFocusBound})")
					else:		print(f"wraparound match (r) found at ({match.a}, {match.b}) - ideal end: {idealFocusBound})")
				else:
					if forward:	print(f"match found at ({match.a}, {match.b}) - ideal start: {idealFocusBound})")
					else:		print(f"match (r) found at ({match.a}, {match.b}) - ideal end: {idealFocusBound})")
		else:
			# TODO - play beep here? change highlight line color?
			self.cleanupDrawings()
			if LOG:
				if forward: print("No match found")
				else: 		print("No match (r) found")



class AlsIncrementalSearch(sublime_plugin.TextCommand):

	def run(self, edit, forward=True):

		viewTarget = ViewEx.getActiveView(self.view.window())

		# --- Detect re-search
		if self.view.element() == ISearch.INPUT_ELEMENT:
			viewExTarget = ViewEx.get(viewTarget)
			print("re-searching")
			viewExTarget.iSearch.search(forward, nudge=True)
			return

		# --- Bail if inside some other special view
		if self.view.element():
			return

		# --- We're just inside a normal view. Open up the search bar go
		viewEx = ViewEx.get(self.view)
		viewEx.markSel.selectPrimaryRegion(MarkAction.SET if viewEx.markSel.isMarkActive() else MarkAction.CLEAR)
		viewEx.iSearch.ensureOpen(forward=forward)



# --- Listeners/Hooks

class AlsEventListener(sublime_plugin.EventListener):
	def on_text_command(self, view, command_name, args):
		LOG = False
		if LOG:	print("[event listener] text_command: " + command_name)

		# NOTE - We only seem to pick up on these in the EventListener, not the ViewEventListener... for whatever reason.

		if view.element() == ISearch.INPUT_ELEMENT:
			return ISearch.on_text_command(view, command_name, args)

		return None

	def on_window_command(self, window, command_name, args):
		LOG = False
		if LOG: print("[event listener] window_command: " + command_name)

		return None

class AlsViewEventListener(sublime_plugin.ViewEventListener):

	def on_text_command(self, command_name, args):
		LOG = False
		if LOG:	print("[view event listener] text_command: " + command_name)
		if self.view.element():	raise AssertionError("Unexpected input view in ViewEventListener...")

		# IMPORTANT - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things, otherwise there is inifnite recursion!

		viewEx = ViewEx.get(self.view)

		# Extend moves

		if command_name == "move" or command_name == "move_to":
			if viewEx.markSel.isMarkActive() and not args.get("extend", False):
				args["extend"] = True
				return (command_name, args)

		# NOTE - on_modified doesn't know what command caused the modification,
		#  We squirrel that info away so on_modified knows if it should drop the selection

		elif command_name == "swap_line_up" or \
			 command_name == "swap_line_down" or \
			 command_name == "indent" or \
			 command_name == "unindent":
			viewEx.markSel.cntModificationToIgnore += 1
			viewEx.markSel.cntWantRefreshMark += 1

		# Execute command unmodified

		return None

	def on_post_text_command(self, command_name, args):
		LOG = False
		if LOG:	print("[view event listener] on_post_text_command")
		if self.view.element():	raise AssertionError("Unexpected input view in ViewEventListener...")

		# NOTE - Commands that don't modify the buffer are handled here.
		#  All buffer-modifying commands are handled in on_modified

		viewEx = ViewEx.get(self.view)

		if command_name == "copy":
			viewEx.markSel.clearAll()

	def on_modified(self):
		LOG = False
		if LOG:	print("[view event listener] on_modified")
		if self.view.element():	raise AssertionError("Unexpected input view in ViewEventListener...")

		# NOTE - Anything that affects contents of buffer will hook into
		#  this function

		viewEx = ViewEx.get(self.view)

		if viewEx.markSel.cntModificationToIgnore > 0:
			viewEx.markSel.cntModificationToIgnore -= 1

		elif viewEx.markSel.isMarkActive():
			viewEx.markSel.clearAll()

	def on_selection_modified(self):
		LOG = False
		if LOG:	print("[view event listener] selection_modified")
		if self.view.element():	raise AssertionError("Unexpected input view in ViewEventListener...")

		# NOTE - Stretch the selection to accomodate incremental search, etc.

		markSel = MarkSel.get(self.view)
		if markSel.isMarkActive():

			if markSel.cntWantRefreshMark > 0:
				# Refresh with new selection that sublime computed

				markSel.placeMark(SelectionAction.KEEP)
				markSel.cntWantRefreshMark -= 1

			else:
				# Expand selection

				markSel.selectPrimaryRegion(MarkAction.SET, asExtension=True)







# --- Example Text Input Handler

# class AlsIncrementalSearch(sublime_plugin.TextCommand):

# 	def input(self, args):
# 		return AlsIncrementalSearchInputHandler(self.view)

# 	def run(self, **kwargs):
# 		print("Running the command")



# class AlsIncrementalSearchInputHandler(sublime_plugin.TextInputHandler):

# 	def __init__(self, view):
# 		self.view = view

# 	def name(self):
# 		return "als_incremental_search"

# 	def placeholder(self):
# 		return "Big Chungus"

# 	def confirm(self, text):
# 		print("Hello from " + text)

# 	def preview(self, text):
# 		print("Current text: " + text)
# 		view.run_command()
# 		return None

