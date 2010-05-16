#! /usr/bin/env python
# encoding: utf-8
# Thomas Nagy, 2007 (ita)

"""
debugging helpers for parallel compilation, outputs
a svg file in the build directory
"""

import time, threading, random, Queue
import Runner, Options, Utils
from Constants import *
from Runner import TaskConsumer

random.seed(100)

WIDTH = 5000
INTERVAL = 0.009
BAND = 22

# red   #ff4d4d
# green #4da74d
# lila  #a751ff

Utils.g_module.title = "Parallel build representation for 'waf -j12 -l4.0 -p' a dual-core cpu, starting with an initial load of 1.0"

mp = {
'cc': '#4da74d',
'cxx': '#4da74d',
'copy_script': '#ff0000',
'cc_link' : '#a751ff',
'static_link': '#a751ff'
}

info = {
'#4da74d': 'Compilation task',
'#cc1d1d': 'Other',
'#a751ff': 'Link task'
}

def map_to_color(name):
	if name in mp:
		return mp[name]
	return "#cc1d1d"


taskinfo = Queue.Queue()
state = 0
def set_running(by, i, tsk):
	taskinfo.put(  (i, id(tsk), time.time(), tsk.__class__.__name__)  )

def newrun(self):

	if 1 == 1:
		while 1:
			tsk = TaskConsumer.ready.get()
			m = tsk.master
			if m.stop:
				m.out.put(tsk)
				continue

			set_running(1, id(self), tsk)
			try:
				tsk.generator.bld.printout(tsk.display())
				if tsk.__class__.stat: ret = tsk.__class__.stat(tsk)
				# actual call to task's run() function
				else: ret = tsk.call_run()
			except Exception, e:
				tsk.err_msg = Utils.ex_stack()
				tsk.hasrun = EXCEPTION

				# TODO cleanup
				m.error_handler(tsk)
				m.out.put(tsk)
				continue

			#time.sleep(1 + 2* random.random())

			if ret:
				tsk.err_code = ret
				tsk.hasrun = CRASHED
			else:
				try:
					tsk.post_run()
				except Utils.WafError:
					pass
				except Exception:
					tsk.err_msg = Utils.ex_stack()
					tsk.hasrun = EXCEPTION
				else:
					tsk.hasrun = SUCCESS
			if tsk.hasrun != SUCCESS:
				m.error_handler(tsk)

			set_running(-1, id(self), tsk)
			m.out.put(tsk)




Runner.TaskConsumer.run = newrun

old_start = Runner.Parallel.start
def do_start(self):
	old_start(self)
	process_colors(taskinfo)
Runner.Parallel.start = do_start

def process_colors(q):
	tmp = []
	try:
		while True:
			(s, t, tm, clsname) = q.get(False)
			tmp.append([s, t, tm, clsname])
	except:
		pass

#file = open('colors.dat', 'rb')
#code = file.read()
#file.close()

#lst = code.strip().split('\n')
#tmp = [x.split() for x in lst]

	try:
		ini = float(tmp[0][2])
	except:
		return

	tmp = [lst[:2] + [float(lst[2]) - ini] + lst[3:] for lst in tmp]

	st = {}
	for l in tmp:
		if not l[0] in st:
			st[l[0]] = len(st.keys())
	tmp = [  [st[lst[0]]] + lst[1:] for lst in tmp ]
	THREAD_AMOUNT = len(st.keys())

	st = {}
	for l in tmp:
		if not l[1] in st:
			st[l[1]] = len(st.keys())
	tmp = [  [lst[0]] + [st[lst[1]]] + lst[2:] for lst in tmp ]


	seen = {}
	acc = []
	for x in xrange(len(tmp)):
		line = tmp[x]
		id = line[1]

		if id in seen:
			continue
		seen[id] = True

		begin = line[2]
		thread_id = line[0]
		for y in xrange(x + 1, len(tmp)):
			line = tmp[y]
			if line[1] == id:
				end = line[2]
				#print id, thread_id, begin, end
				#acc.append(  ( 10*thread_id, 10*(thread_id+1), 10*begin, 10*end ) )
				acc.append( (BAND * begin, BAND*thread_id, BAND*end - BAND*begin, BAND, line[3]) )
				break

	gwidth = 0
	for x in tmp:
			m = BAND * x[2]
			if m > gwidth: gwidth = m

	ratio = float(WIDTH) / gwidth
	gwidth = WIDTH

	gheight = BAND * (THREAD_AMOUNT + len(info.keys()) + 1.5)

	out = []

	out.append("""<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>
<!DOCTYPE svg PUBLIC \"-//W3C//DTD SVG 1.0//EN\"
\"http://www.w3.org/TR/2001/REC-SVG-20010904/DTD/svg10.dtd\">
<svg xmlns=\"http://www.w3.org/2000/svg\" xmlns:xlink=\"http://www.w3.org/1999/xlink\" version=\"1.0\"
   x=\"%r\" y=\"%r\" width=\"%r\" height=\"%r\"
   id=\"svg602\" xml:space=\"preserve\">
<defs id=\"defs604\" />\n""" % (-1, -1, gwidth + 3, gheight + 2))

	# main title
	out.append("""<text x="%d" y="%d" style="font-size:15px; text-anchor:middle; font-style:normal;font-weight:normal;fill:#000000;fill-opacity:1;stroke:none;stroke-width:1px;stroke-linecap:butt;stroke-linejoin:miter;stroke-opacity:1;font-family:Bitstream Vera Sans">%s</text>
""" % (gwidth/2, gheight - 5, Utils.g_module.title))

	# the rectangles
	for (x, y, w, h, clsname) in acc:
		out.append("""<rect
   x='%r' y='%r'
   width='%r' height='%r'
   style=\"font-size:10;fill:%s;fill-opacity:1.0;fill-rule:evenodd;stroke:#000000;\"
   />\n""" % (x*ratio, y, w*ratio, h, map_to_color(clsname)))

	# output the caption
	cnt = THREAD_AMOUNT
	for (color, text) in info.iteritems():
		# caption box
		b = BAND/2
		out.append("""<rect
		x='%r' y='%r'
		width='%r' height='%r'
		style=\"font-size:10;fill:%s;fill-opacity:1.0;fill-rule:evenodd;stroke:#000000;\"
  />\n""" % (BAND, (cnt + 0.5) * BAND, b, b, color))

		# caption text
		out.append("""<text
   style="font-size:12px;font-style:normal;font-weight:normal;fill:#000000;fill-opacity:1;stroke:none;stroke-width:1px;stroke-linecap:butt;stroke-linejoin:miter;stroke-opacity:1;font-family:Bitstream Vera Sans"
   x="%r" y="%d">%s</text>\n""" % (2 * BAND, (cnt+1) * BAND, text))
		cnt += 1

	out.append("\n</svg>")

	file = open("foo.svg", "wb")
	file.write("".join(out))
	file.close()

	import os
	os.popen("convert foo.svg foo.png").read()

