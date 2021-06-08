# TODO
# - End incremental search if I press a navigation key
# - Better streamlined find/replace with yn.! options
# - (Experiment) Drop mark after incremental search selection?
# - Mark ring
# - Jump focus to other sublime window (not just other view)
# - Consolidate all open tabs to 1 window. Option to close duplicates?
# - Force h on left and cpp on right?

import sublime
import sublime_plugin

import traceback #debug

# --- Extra state stored per view to enable behavior similar to emacs 'trasient-mark-mode'

g_viewExDict = {}

class ViewEx():
	def __init__(self, view, forward=True):
		self.markSel = MarkSel(view)
		self.iSearch = ISearch(view, self.markSel, forward)

	@staticmethod
	def ensure(view, forward=True):
		result = g_viewExDict.get(view.id())

		if result is None:
			result = ViewEx(view, forward)
			g_viewExDict[view.id()] = result

		return result

	# TODO - Delete from dict if view goes away?

# --- Mark/selection

class MarkSel():

	FALLBACK_REGION = sublime.Region(0, 0)

	@staticmethod
	def ensure(view):
		return ViewEx.ensure(view).markSel


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

	def primaryCursor(self):
		return self.selection[0].b if len(self.selection) > 0 else 0

	def primaryRegion(self):
		return MarkSel.ensureRegionValid(self.selection[0] if len(self.selection) > 0 else FALLBACK_REGION) # @Slow - Redundant checks

	def isMarkActive(self):
		return self.mark >= 0 and len(self.selection) == 1

	def placeMark(self, clearSelection):
		self.mark = self.primaryCursor()

		if clearSelection:
			self.selection.clear()
			self.selection.add(sublime.Region(self.mark, self.mark))

	def select(self, region, wantMark, wantShow=True):
		# HMM - Maybe we should only validate here...?
		self.selection.clear()
		self.selection.add(region)
		if wantMark:
			self.placeMark(clearSelection=False)
		else:
			self.clearMark()

		if wantShow:
			self.view.show(region)

		return region

	def selectPrimaryRegion(self, wantMark):
		return self.select(self.primaryRegion(), wantMark)

	def clearAll(self):
		cursor = self.primaryCursor()
		self.select(sublime.Region(cursor, cursor), wantMark=False)

	def isSingleNonEmptySelection(self):
		return len(self.selection) == 1 and self.selection[0].a != self.selection[0].b

	def isSingleEmptySelection(self):
		return len(self.selection) == 1 and self.selection[0].a == self.selection[0].b



# --- Text Commands (Prefixed with 'Als' to distinguish between my commands and built-in sublime commands)

class AlsSetMark(sublime_plugin.TextCommand):
	"""Sets the mark"""
	def run(self, edit):
		print("running set mark")
		markSel = MarkSel.ensure(self.view)
		markSel.placeMark(clearSelection=True)

class AlsClearSelection(sublime_plugin.TextCommand):

	"""Clears the selection (and the mark)"""
	def run(self, edit):
		markSel = MarkSel.ensure(self.view)
		markSel.clearAll()


class AlsInflateSelectionToFillLines(sublime_plugin.TextCommand):

	"""Inflates the selection (and the mark) to the beginning/end of the lines at the beginning/end of the selection"""
	def run(self, edit):
		markSel = MarkSel.ensure(self.view)

		region = markSel.selection[0]
		isForwardOrEmpty = (region.a <= region.b)

		# NOTE - view.line(..)
		#  - inflates region (good)
		#  - omits trailing \n (good),
		#  - does NOT maintain reversed-ness (bad)
		newRegion = self.view.line(region)

		# ... so we fix it up
		if not isForwardOrEmpty:
			newRegion = sublime.Region(newRegion.b, newRegion.a)

		markSel.select(newRegion, wantMark=markSel.isMarkActive())

# class AlsHidePanelThenRun(sublime_plugin.TextCommand):
# 	"""Auto-close a panel and jump right back into the normal view with a command"""
# 	def run(self, edit, **kwargs):
# 		print("Running als_hide_panel_then_run_command")
# 		if self.view.element() != "incremental_find:input":
# 			raise AssertionError("Illegal context for this command")

# 		command_name = kwargs.pop('command_name', None)
# 		w = self.view.window()
# 		w.run_command("hide_panel")
# 		w.run_command(command_name, kwargs)


# --- I-Search

# TODO
# Reverse search
# ‘Overwrapped’ search, which means that you are revisiting matches that you have already seen (i.e., starting from cursorOnOpen)


class ISearch():
	INPUT_ELEMENT = "input:input"
	PANEL_NAME = "i-search"
	FOUND_REGION_NAME = "als_i_search_found"
	FOCUS_REGION_NAME = "als_i_search_focus"
	NO_FOCUS = sublime.Region(-1, -1)

	@staticmethod
	def ensure(view):
		return ViewEx.ensure(view).iSearch

	def __init__(self, view, markSel, forward):
		self.view = view
		self.markSel = markSel
		self.reset(openPanel=False)
		self.forward = forward # NOTE - can toggle each search!

	def reset(self, openPanel=False):
		self.cursorOnOpen = -1
		self.focus = sublime.Region(-1, -1)
		self.text = ""
		self.cleanupDrawings()
		self.inputView = None
		if openPanel:
			self.inputView = self.view.window().show_input_panel(self.PANEL_NAME, "", self.onDone, self.onChange, self.onCancel)

	def forceClose(self):
		self.window.run_command("hide_panel")
		self.reset()

	def cleanupDrawings(self):
		self.view.erase_regions(self.FOUND_REGION_NAME)
		self.view.erase_regions(self.FOCUS_REGION_NAME)

	# --- Hooks

	def onDone(self, text):
		self.reset()

	def onChange(self, text):

		if len(self.markSel.selection) != 1:
			self.forceClose()
			return

		print("setting text to: " + text)
		self.text = text
		idealFocusStart = self.focus.a if self.focus.a != -1 else self.markSel.primaryCursor()
		self.search(idealFocusStart)

	def onCancel(self):
		self.cleanupDrawings()

	# --- Operations

	def search(self, forward, nudge=False):
		LOG = True

		if not self.text:
			return

		primaryRegion = self.markSel.selectPrimaryRegion(wantMark=True)

		idealFocusBound = None
		if forward:
			idealFocusBound = primaryRegion.begin() + (1 if nudge else 0)
		else:
			idealFocusBound = primaryRegion.end() - (1 if nudge else 0)

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

			if not bestMatch: raise AssertionError("Why didn't we find a best match?")
			# --- Lock in the match

			self.focus = bestMatch
			self.markSel.select(self.focus, wantMark=True, wantShow=True) # HMM - want mark here? # HMM - want to extend selection here?

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
			self.cleanupDrawings()
			if LOG:
				if forward: print("No match found")
				else: 		print("No match (r) found")

class AlsIncrementalSearch(sublime_plugin.TextCommand):

	def run(self, edit, forward=True):

		# --- Detect re-search
		if self.view.element() == ISearch.INPUT_ELEMENT:
			print("researching")

			window = self.view.window()
			viewSearchTarget = window.active_view()
			if viewSearchTarget.element():
				raise AssertionError("Unable to bootstrap access to view being edited (stuck with active_view: " + str(viewSearchTarget.element) + ")")

			viewEx = ViewEx.ensure(viewSearchTarget, forward=forward)
			viewEx.iSearch.search(forward, nudge=True)
			return

		# --- Bail if inside some other special view
		if self.view.element():
			return

		viewEx = ViewEx.ensure(self.view, forward=forward)
		viewEx.markSel.selectPrimaryRegion(wantMark=True)
		viewEx.iSearch.reset(openPanel=True)



# --- Listeners/Hooks

class AlsEventListener(sublime_plugin.EventListener):
	def on_text_command(self, view, command_name, args):
		if False:
			print("[event listener] text_command: " + command_name)

	def on_window_command(self, window, command_name, args):
		if False:
			print("[event listener] window_command: " + command_name)

class AlsViewEventListener(sublime_plugin.ViewEventListener):

	def on_text_command(self, command_name, args):
		# IMPORTANT - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things, otherwise there is inifnite recursion!

		if False:
			print("[view event listener] text_command: " + command_name)

		viewEx = ViewEx.ensure(self.view)

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

	# TODO - Get this hook running on incremental find window!!!
	def on_post_text_command(self, command_name, args):
		if False:
			print("[view event listener] on_post_text_command")

		# NOTE - Commands that don't modify the buffer are handled here.
		#  All buffer-modifying commands are handled in on_modified

		viewEx = ViewEx.ensure(self.view)

		if command_name == "copy":
			viewEx.markSel.clearAll()

		# TODO - Remove if I get i-search up and running
		# isMoveCommand = (command_name == "move" or command_name == "move_to")
		# if viewEx.markSel.isSingleNonEmptySelection() and isMoveCommand and (self.view.element() == "incremental_find:input"):
		# 	window = view.window()
		# 	view.close() 							# Close the incremental find view
		# 	window.run_command(command_name, args) 	# Forward command view that sublime gives focus to

	def on_modified(self):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function
		if False:
			print("[view event listener] on_modified")

		viewEx = ViewEx.ensure(self.view)

		if viewEx.markSel.cntModificationToIgnore > 0:
			viewEx.markSel.cntModificationToIgnore -= 1

		elif viewEx.markSel.isMarkActive():
			viewEx.markSel.clearAll()

	def on_selection_modified(self):
		# viewEx = ViewEx.ensure(self.view)
		# selection = self.view.sel()

		markSel = MarkSel.ensure(self.view)

		# NOTE - Stretch the selection to accomodate incremental search, etc.

		if markSel.isMarkActive():
			region = markSel.primaryRegion()

			if markSel.cntWantRefreshMark > 0:
				# If refreshing the mark, we trust sublime's built-in region stuff to
				#  move an entire selection, and re-plop the mark there

				markSel.placeMark(clearSelection=False)
				markSel.cntWantRefreshMark -= 1

			# else:
			# 	# If not refreshing the mark, then we keep it in place and expand to it

			# 	if markSel.mark < region.begin():
			# 		markSel.select(sublime.Region(markSel.mark, region.end
			# 		selection.clear()
			# 		selection.add(sublime.Region(viewEx.mark, region.end()))

			# 	elif viewEx.mark > region.end():
			# 		selection.clear()
			# 		selection.add(sublime.Region(viewEx.mark, region.begin()))

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

