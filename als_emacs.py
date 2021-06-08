# TODO
# - End incremental search if I press a navigation key
# - Better streamlined find/replace with yn.! options
# - Mark ring

import sublime
import sublime_plugin

# --- Extra state stored per view to enable behavior similar to emacs 'trasient-mark-mode'

g_viewExDict = {}

class ViewEx():
	def __init__(self):
		clearMark(self)

def ensureViewEx(view):
	result = g_viewExDict.get(view.id())

	if result is None:
		result = ViewEx()
		g_viewExDict[view.id()] = result

	return result

# --- Mark/selection operations

def isMarkActive(viewEx, selection):
	return viewEx.mark != -1 and len(selection) == 1

def clearMark(viewEx):
	if False: # debug
		print("CLR")
	viewEx.mark = -1
	viewEx.cntModificationToIgnore = 0
	viewEx.cntWantRefreshMark = 0

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

class AlsTrySetMarkCommand(sublime_plugin.TextCommand):
	"""Sets the mark if there is an unambiguous cursor position (no selection, no multi-cursor)"""
	def run(self, edit):
		selection = self.view.sel()

		if len(selection) == 1:
			placeMark(ensureViewEx(self.view), selection, selection[0].b)

class AlsClearSelectionCommand(sublime_plugin.TextCommand):
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

class AlsTestInputHandlerCommand(sublime_plugin.WindowCommand):
	def run(self):
		pass

	def input(self, arg):
		# return AlsTextInputHandler()
		pass

# --- Listeners
# NOTE - Prefixed with Als to distinguish between my commands and built-in sublime commands

class AlsViewEventListener(sublime_plugin.ViewEventListener):
	def on_text_command(self, command_name, args):
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

	def on_post_text_command(self, command_name, args):
		# NOTE - Commands that don't modify the buffer are handled here.
		#  All buffer-modifying commands are handled in on_modified

		viewEx = ensureViewEx(self.view)
		selection = self.view.sel()

		if command_name == "copy":
			clearSelectionToSingleCursor(ensureViewEx(self.view), self.view.sel())

	# def on_window_command(self, window, command_name, args):
		# pass

	def on_modified(self):
		# NOTE - Anything that affects contents of buffer will hook into
		#  this function

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

# class AlsTextInputHandler(sublime_plugin.TextInputHandler):
# 	def name(self):
# 		return "this string is the name of the argument that is being passed to the command"

# 	def placeholder(self):
# 		return "Big Chungus"

# 	def confirm(self, text):
# 		print(text)

