-------------------------------------------------------------------------
--	Frontend probe: logs a line whenever something in the main menu is
--	clicked, with the id of what was clicked (context.string carries
--	the component id, per CA's own autorun.lua usage).
-------------------------------------------------------------------------

local M = {};

local EVENT_NAMES = {
	"ComponentLClickUp",		-- any left-click on a UI component
	"ComponentLinkClicked",
	"UICreated",
	"FrontendScreenTransition",
};

function M.init(core)
	for i = 1, #EVENT_NAMES do
		local name = EVENT_NAMES[i];
		if events[name] then
			events[name][#events[name] + 1] = core.guarded(name, function(context)
				local what = "?";
				pcall(function() what = tostring(context.string); end);
				core.log(name .. ": " .. what);
			end);
		else
			core.log("no such event table: " .. name);
		end;
	end;
	core.log("frontend click listeners registered");
end;

return M;
