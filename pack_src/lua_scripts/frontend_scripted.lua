
print("frontend_scripted.lua loaded");

system.ClearRequiredFiles();

package.path = ";?.lua;data/ui/templates/?.lua;data/ui/?.lua"

require "data.lua_scripts.all_scripted"

events = get_events();

local advice = require "data.lua_scripts.export_advice"

local m_user_defined_event_callbacks = {}

function AddEventCallBack(event, func, add_to_user_defined_list)
	assert(events[event] ~= nil, "Attempting to add event callback to non existant event ("..event..")")
	assert(func ~= nil, "Attempting to add a non existant function to event "..event)

	-- Push the function to the back of the list of function for the specified address	
	events[event][#events[event]+1] = func
	
	if add_to_user_defined_list ~= false then
		m_user_defined_event_callbacks[#m_user_defined_event_callbacks+1] = {}
		m_user_defined_event_callbacks[#m_user_defined_event_callbacks].event = event
		m_user_defined_event_callbacks[#m_user_defined_event_callbacks].func = func
	end
end



--
--	Script support for historic battle and prologue frontend
-- 

require "lua_scripts.FE_Script_Header";
eh = event_handler:new(AddEventCallBack);
tm = timer_manager:new(Timers);
svr = ScriptedValueRegistry:new();
m_root = nil;

require "lua_scripts.frontend_prologue"
require "lua_scripts.frontend_hbs"


--
--	Create handle to the UI root when it's created
--

eh:add_listener(
	"OnUICreated",
	"UICreated",
	true,
	function(context) OnUICreated(context) end,
	false
);

	
function OnUICreated(context)	
	if context then
		m_root = UIComponent(context.component);
	end;
end

-------------------------------------------------------
--	ATTILA-AI hook (only addition to the vanilla file;
--	everything above is a verbatim copy from data.pack)
-------------------------------------------------------

package.path = package.path .. ";data/aai/?.lua";

local aai_ok, aai_err = pcall(require, "aai_frontend");

if not aai_ok then
	local aai_f = io.open("data/attila_ai_log.txt", "a");
	if aai_f then
		aai_f:write("LOADER ERROR (frontend_scripted.lua): " .. tostring(aai_err) .. "\n");
		aai_f:close();
	end;
end;
