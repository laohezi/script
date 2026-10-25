-- 视频压缩配置窗口（原生 macOS 控件）
--
-- 由 mac/quick_action.py 通过 osascript 调用。
-- 传入: argv 第 1 项 = 已选中项目的数量文字
-- 返回: "crf|分辨率|硬件加速"  例如 "28|1080p|1"；用户取消时返回 "CANCEL"
--       （分辨率为空字符串表示保持原始尺寸）

use framework "Cocoa"
use scripting additions

property crfButtons : {}
property customField : missing value
property resolutionPopup : missing value
property hardwareCheckbox : missing value
property alertObject : missing value

property viewWidth : 440
property viewHeight : 220


on run argv
	set theAlert to current application's NSAlert's alloc()'s init()
	theAlert's setMessageText:"视频压缩"
	theAlert's setInformativeText:"已选中 " & (item 1 of argv) & " 个项目"
	theAlert's setAlertStyle:1
	theAlert's addButtonWithTitle:"压缩"
	theAlert's addButtonWithTitle:"取消"

	set accView to current application's NSView's alloc()'s initWithFrame:{origin:{x:0, y:0}, ¬
		|size|:{width:(my viewWidth), height:(my viewHeight)}}
	buildControls(accView)
	theAlert's setAccessoryView:accView

	-- Esc 等同取消
	set cancelButton to (theAlert's buttons())'s objectAtIndex:1
	cancelButton's setKeyEquivalent:(character id 27)

	-- 快速操作由 Finder 触发，必须显式激活否则窗口会被压在后台
	set theApp to current application's NSApplication's sharedApplication()
	theApp's setActivationPolicy:1
	theApp's activateIgnoringOtherApps:true

	-- 激活与窗口显示之间存在竞态；runModal 的模态循环里再补一次，
	-- 否则窗口可能可见但不持有键盘焦点（回车/esc 失效）
	set my alertObject to theAlert
	current application's NSTimer's scheduledTimerWithTimeInterval:0.2 ¬
		target:me selector:"activateWindow:" userInfo:(missing value) repeats:false

	set response to theAlert's runModal()
	if (response as integer) is 1001 then return "CANCEL"

	set crf to 28
	repeat with btn in my crfButtons
		if (btn's state()) is 1 then set crf to (btn's tag()) as integer
	end repeat

	set customText to ((my customField's stringValue()) as text)
	if customText is not "" then
		try
			set crf to customText as integer
		end try
	end if

	set resolution to (my resolutionPopup's titleOfSelectedItem()) as text
	if resolution is "保持原始" then set resolution to ""

	return (crf as text) & "|" & resolution & "|" & ((my hardwareCheckbox's state()) as text)
end run


on activateWindow:aTimer
	current application's NSApplication's sharedApplication()'s activateIgnoringOtherApps:true
	(my alertObject's window())'s makeKeyAndOrderFront:me
end activateWindow:


on buildControls(parent)

	makeLabel(parent, "清晰度 (CRF)", 0, 202, 200, 16, 12, true, false)

	set my crfButtons to {}
	set presets to {{"近无损 (CRF 18)", 18, 0, 176}, ¬
		{"高画质 (CRF 23)", 23, 0, 154}, ¬
		{"标准 (CRF 28)", 28, 0, 132}, ¬
		{"小体积 (CRF 32)", 32, 195, 176}, ¬
		{"极小体积 (CRF 35)", 35, 195, 154}}
	repeat with preset in presets
		set btn to makeRadio(parent, item 1 of preset, item 3 of preset, item 4 of preset, 190, item 2 of preset)
		if (item 2 of preset) is 28 then btn's setState:1
		set end of my crfButtons to btn
	end repeat

	makeLabel(parent, "自定义 CRF", 0, 104, 90, 16, 12, false, false)
	set my customField to makeField(parent, 92, 101, 112, "留空则用预设")
	makeLabel(parent, "范围 0-51，越小画质越好", 214, 104, 220, 16, 11, false, true)

	makeLabel(parent, "目标分辨率", 0, 70, 90, 16, 12, false, false)
	set my resolutionPopup to makePopup(parent, 92, 66, 130, ¬
		{"保持原始", "2160p", "1080p", "720p", "480p"})
	makeLabel(parent, "只缩小，不放大", 230, 70, 200, 16, 11, false, true)

	set my hardwareCheckbox to makeCheckbox(parent, 0, 36, 430, ¬
		"硬件加速（自动检测，失败回退软件编码）")
	my hardwareCheckbox's setState:1

	makeLabel(parent, "文件夹：递归输出到 <文件夹名>_compressed/　　文件：输出到源文件同目录", ¬
		0, 12, 440, 14, 10, false, true)
	return
end buildControls


on makeLabel(parent, theText, x, y, w, h, theSize, isBold, isDim)
	set lbl to current application's NSTextField's alloc()'s initWithFrame:{origin:{x:x, y:y}, ¬
		|size|:{width:w, height:h}}
	lbl's setStringValue:theText
	lbl's setBezeled:false
	lbl's setDrawsBackground:false
	lbl's setEditable:false
	lbl's setSelectable:false
	if isBold then
		lbl's setFont:(current application's NSFont's boldSystemFontOfSize:theSize)
	else
		lbl's setFont:(current application's NSFont's systemFontOfSize:theSize)
	end if
	if isDim then lbl's setTextColor:(current application's NSColor's secondaryLabelColor())
	parent's addSubview:lbl
	return lbl
end makeLabel


on makeRadio(parent, theText, x, y, w, theTag)
	set btn to current application's NSButton's alloc()'s initWithFrame:{origin:{x:x, y:y}, ¬
		|size|:{width:w, height:20}}
	btn's setButtonType:4
	btn's setTitle:theText
	btn's setFont:(current application's NSFont's systemFontOfSize:12)
	btn's setTag:theTag
	parent's addSubview:btn
	return btn
end makeRadio


on makeCheckbox(parent, x, y, w, theText)
	set btn to current application's NSButton's alloc()'s initWithFrame:{origin:{x:x, y:y}, ¬
		|size|:{width:w, height:20}}
	btn's setButtonType:3
	btn's setTitle:theText
	btn's setFont:(current application's NSFont's systemFontOfSize:12)
	parent's addSubview:btn
	return btn
end makeCheckbox


on makePopup(parent, x, y, w, titles)
	set pop to current application's NSPopUpButton's alloc()'s initWithFrame:{origin:{x:x, y:y}, ¬
		|size|:{width:w, height:26}} pullsDown:false
	pop's addItemsWithTitles:titles
	pop's setFont:(current application's NSFont's systemFontOfSize:12)
	parent's addSubview:pop
	return pop
end makePopup


on makeField(parent, x, y, w, placeholder)
	set fld to current application's NSTextField's alloc()'s initWithFrame:{origin:{x:x, y:y}, ¬
		|size|:{width:w, height:22}}
	fld's setPlaceholderString:placeholder
	fld's setFont:(current application's NSFont's systemFontOfSize:12)
	fld's setAlignment:1
	parent's addSubview:fld
	return fld
end makeField
