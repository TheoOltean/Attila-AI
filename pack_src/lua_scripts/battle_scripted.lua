
print("battle_scripted.lua loaded");


system.ClearRequiredFiles();

package.path = ";?.lua;data/ui/templates/?.lua;data/ui/?.lua"

require "data.lua_scripts.all_scripted"
require "data.lua_scripts.export_advice"

events = get_events();




local m_user_defined_event_callbacks = {}

function AddEventCallBack(event, func, add_to_user_defined_list)
	print(event, func, add_to_user_defined_list)
	
	assert(events[event] ~= nil, "Attempting to add event callback to non existent event ("..event..")")
	assert(func ~= nil, "Attempting to add a non existent function to event "..event)

	-- Push the function to the back of the list of function for the specified address	
	events[event][#events[event]+1] = func
	
	if add_to_user_defined_list ~= false then
		m_user_defined_event_callbacks[#m_user_defined_event_callbacks+1] = {}
		m_user_defined_event_callbacks[#m_user_defined_event_callbacks].event = event
		m_user_defined_event_callbacks[#m_user_defined_event_callbacks].func = func
	
	end
	
end

function ClearEventCallbacks()	
	for i,v in ipairs(m_user_defined_event_callbacks) do
		local new_event_table = {}
		for ei, ev in ipairs(events[v.event]) do
			if ev ~= v.func then
				new_event_table[#new_event_table+1] = ev
			end
		end
		events[v.event] = new_event_table
	end
	
	m_user_defined_event_callbacks = {}
end

-------------------------------------------------------
--	ATTILA-AI hook (only addition to the vanilla file;
--	everything above is a verbatim copy from data.pack)
-------------------------------------------------------

package.path = package.path .. ";data/aai/?.lua";

local aai_ok, aai_err = pcall(require, "aai_battle");

if not aai_ok then
	local aai_f = io.open("data/attila_ai_log.txt", "a");
	if aai_f then
		aai_f:write("LOADER ERROR (battle_scripted.lua): " .. tostring(aai_err) .. "\n");
		aai_f:close();
	end;
end;
