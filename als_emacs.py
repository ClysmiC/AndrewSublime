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

# --- Extra state stored per view to enable behavior similar to emacs 'trasient-mark-mode'

g_viewExDict = {}

class ViewEx():
	def __init__(self):
		clearMark(self)

def clearMark(viewEx):
	if False:
		print("CLR")
	viewEx.mark = -1
	viewEx.cntModificationToIgnore = 0
	viewEx.cntWantRefreshMark = 0

def ensureViewEx(view):
	result = g_viewExDict.get(view.id())

	if result is None:
		result = ViewEx()
		g_viewExDict[view.id()] = result

	return result

# --- Mark/selection operations

def isMarkActive(viewEx, selection):
	return viewEx.mark != -1 and len(selection) == 1

def isSingleNonEmptySelection(selection):
	return len(selection) == 1 and selection[0].a != selection[0].b

def clearSelectionToSingleCursor(viewEx, selection, shouldClearMark=True):
	assert len(selection) > 0

	# NOTE - Clobbers existing selections and multi-cursor
	region = selection[0]
	selection.clear()
	selection.add(sublime.Region(region.b, region.b))

	if shouldClearMark:
		clearMark(viewEx)

def placeMark(viewEx, selection, mark):
	assert mark >= 0

	clearSelectionToSingleCursor(viewEx, selection, shouldClearMark=False)
	viewEx.mark = mark

def matchMarkToSelection(viewEx, selection):
	viewEx.mark = selection[0].a


# --- Text Commands (Prefixed with 'Als' to distinguish between my commands and built-in sublime commands)

class AlsTrySetMark(sublime_plugin.TextCommand):
	"""Sets the mark if there is an unambiguous cursor position (no selection, no multi-cursor)"""
	def run(self, edit):
		selection = self.view.sel()

		if len(selection) == 1:
			placeMark(ensureViewEx(self.view), selection, selection[0].b)

class AlsClearSelection(sublime_plugin.TextCommand):

	"""Clears the selection (and the mark) if they are active."""
	def run(self, edit):
		clearSelectionToSingleCursor(ensureViewEx(self.view), self.view.sel())

class AlsExpandSelectionToFillLines(sublime_plugin.TextCommand):

	"""Expands the selection (and the mark) to the beginning/end of the lines at the beginning/end of the selection"""
	def run(self, edit):
		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		if isMarkActive(viewEx, selection):
			region = selection[0]
			isForwardOrEmpty = (region.a <= region.b)

			# NOTE - view.line(..)
			#  - expands region (good)
			#  - omits trailing \n (good),
			#  - does NOT maintain reversed-ness (bad)
			newRegion = self.view.line(region)

			# ... so we fix it up
			if not isForwardOrEmpty:
				newRegion = sublime.Region(newRegion.b, newRegion.a)

			selection.clear()
			selection.add(newRegion)
			matchMarkToSelection(viewEx, selection)

class AlsHidePanelThenRun(sublime_plugin.TextCommand):
	"""Auto-close a panel and jump right back into the normal view with a command"""
	def run(self, edit, **kwargs):
		print("Running als_hide_panel_then_run_command")
		if self.view.element() != "incremental_find:input":
			raise AssertionError("Illegal context for this command")

		command_name = kwargs.pop('command_name', None)
		w = self.view.window()
		w.run_command("hide_panel")
		w.run_command(command_name, kwargs)


# --- I-Search

INPUT_ELEMENT = "input:input"
I_SEARCH_PANEL = "i-search"
I_SEARCH_FOUND_REGION = "als_i_search_found"
I_SEARCH_FOCUS_REGION = "als_i_search_focus"

class AlsIncrementalSearch(sublime_plugin.TextCommand):

	def run(self, edit):
		self.viewEx = None
		self.cursorAtStart = -1
		self.focus = sublime.Region(-1, -1)

		if self.view.element() == INPUT_ELEMENT:
			print("repeat search")
			return

		if self.view.element() is not None:
			return

		self.viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		if len(selection) != 1: # no-op if multi-cursor active
			return

		cursorAtStart = selection[0].b # HMM - Should we capture the whole selection region at the start?
		self.view.window().show_input_panel(I_SEARCH_PANEL, "", self.onDone, self.onChange, self.onCancel)

	def onDone(self, text):
		self.cleanup()

	def onChange(self, text):
		if not text:
			return

		selection = self.view.sel()

		if len(selection) != 1:
			self.forceClose()
			return

		cursor = selection[0].b
		found = self.view.find_all(text, flags=sublime.LITERAL)


		if len(found) > 0:
			idealFocus = self.cursorAtStart

			matchPrev = sublime.Region(-1, -1)
			idealMatch = None

			for match in found:

				if match.b < match.a:
					raise AssertionError("find_all returned reversed region?")

				if match.a <= matchPrev.a:
					raise AssertionError("find_all returned unsorted list?")

				if match.a >= idealFocus:
					idealMatch = match
					break

			if idealMatch:
				self.focus = idealMatch
				self.view.add_regions(
					I_SEARCH_FOCUS_REGION,
					[self.focus],
					scope="region.greenish")
			else:
				pass # TODO - wrap-around search? idk

			# TODO - Make color match theme instead of hard-coding
			self.view.add_regions(
				I_SEARCH_FOUND_REGION,
				found,
				scope="region.greenish",
				flags=sublime.DRAW_NO_FILL)

	def onCancel(self):
		self.cleanup()

	def forceClose(self):
		if False:
			print("force closing " + I_SEARCH_PANEL)

		self.window.run_command("hide_panel")
		self.cleanup()

	def cleanup(self):
		self.view.erase_regions(I_SEARCH_FOUND_REGION)
		self.view.erase_regions(I_SEARCH_FOCUS_REGION)



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
		if False:
			print("[view event listener] text_command: " + command_name)

		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		# NOTE - Altering the return value feeds the altered command back into this function.
		#  Only return a tuple if you have actually altered things, otherwise there is inifnite recursion!

		if command_name == "move" or command_name == "move_to":
			if isMarkActive(viewEx, selection) and not args.get("extend", False):
				args["extend"] = True
				return (command_name, args)

		# NOTE - on_modified doesn't know what command caused the modification,
		#  We squirrel that info away so on_modified knows if it should drop the selection

		elif command_name == "swap_line_up" or \
			 command_name == "swap_line_down" or \
			 command_name == "indent" or \
			 command_name == "unindent":
			viewEx.cntModificationToIgnore += 1
			viewEx.cntWantRefreshMark += 1

		# Execute command unmodified

		return None

	# TODO - Get this hook running on incremental find window!!!
	def on_post_text_command(self, command_name, args):
		if False:
			print("[view event listener] on_post_text_command")

		# NOTE - Commands that don't modify the buffer are handled here.
		#  All buffer-modifying commands are handled in on_modified

		view = self.view
		viewEx = ensureViewEx(view)
		selection = view.sel()

		if command_name == "copy":
			clearSelectionToSingleCursor(viewEx, selection)

		isMoveCommand = (command_name == "move" or command_name == "move_to")

		if isSingleNonEmptySelection(view) and isMoveCommand and (view.element() == "incremental_find:input"):
			window = view.window()
			view.close() 							# Close the incremental find view
			window.run_command(command_name, args) 	# Forward command view that sublime gives focus to

	def on_modified(self):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function
		if False:
			print("[view event listener] on_modified")

		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		if viewEx.cntModificationToIgnore > 0:
			viewEx.cntModificationToIgnore -= 1

		elif isMarkActive(viewEx, selection):
			clearSelectionToSingleCursor(ensureViewEx(self.view), self.view.sel())

	def on_selection_modified(self):
		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		# NOTE - Stretch the selection to accomodate incremental search, etc.

		if isMarkActive(viewEx, selection):
			region = selection[0]

			if viewEx.cntWantRefreshMark > 0:
				# If refreshing the mark, we trust sublime's built-in region stuff to
				#  move an entire selection, and re-plop the mark there

				matchMarkToSelection(viewEx, selection)
				viewEx.cntWantRefreshMark -= 1

			else:
				# If not refreshing the mark, then we keep it in place and expand to it

				if viewEx.mark < region.begin():
					selection.clear()
					selection.add(sublime.Region(viewEx.mark, region.end()))

				elif viewEx.mark > region.end():
					selection.clear()
					selection.add(sublime.Region(viewEx.mark, region.begin()))

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

