import sublime
import sublime_plugin

isOn = False

class ExampleCommand(sublime_plugin.TextCommand):
	def run(self, edit):
		global isOn
		isOn = not isOn
		if isOn:
			self.view.insert(edit, 0, "Hello, World!")
		else:
			self.view.erase(edit, sublime.Region(0, len("Hello, World!")))
