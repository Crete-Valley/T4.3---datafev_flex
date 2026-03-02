import React from 'react';
import { Database, TrendingUp, Send, Settings, BarChart3 } from 'lucide-react';

export default function EVFlexFlowchart() {
  return (
    <div className="w-full h-screen bg-gradient-to-br from-slate-900 via-blue-900 to-slate-900 p-4 md:p-8 overflow-auto">
      <div className="max-w-7xl mx-auto">
        {/* Title */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">
            datafev_flex Service Flowchart
          </h1>
          <p className="text-blue-200">Stage-1 + Stage-2 (Strict / Best-Effort) + Tracking Analytics</p>
        </div>

        {/* Flowchart */}
        <div className="flex flex-col items-center space-y-6">
          
          {/* Stage 0: Input Data */}
          <div className="bg-gradient-to-r from-emerald-500 to-emerald-600 rounded-xl p-6 shadow-2xl w-full max-w-2xl border-2 border-emerald-300">
            <div className="flex items-center gap-3 mb-3">
              <Database className="w-8 h-8 text-white" />
              <h2 className="text-2xl font-bold text-white">INPUT DATA (XLSX)</h2>
            </div>
            <div className="bg-white/10 rounded-lg p-4 backdrop-blur">
              <p className="text-white font-semibold mb-2">📊 Data Sources:</p>
              <ul className="text-white space-y-1 ml-4">
                <li>• Forecasted Day-Ahead EV Fleet Profiles</li>
                <li>• Charger Cluster Configuration</li>
              </ul>
              <div className="mt-3 pt-3 border-t border-white/20">
                <p className="text-emerald-100 font-mono text-sm">parse_xlsx_input() → EVFleet + MultiClusterSystem</p>
              </div>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex flex-col items-center">
            <div className="w-1 h-12 bg-gradient-to-b from-blue-400 to-purple-400"></div>
            <div className="w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-purple-400"></div>
          </div>

          {/* Stage 1: Flex Potential */}
          <div className="bg-gradient-to-r from-purple-600 to-indigo-600 rounded-xl p-6 shadow-2xl w-full max-w-3xl border-2 border-purple-300">
            <div className="flex items-center gap-3 mb-3">
              <TrendingUp className="w-8 h-8 text-white" />
              <h2 className="text-2xl font-bold text-white">STAGE 1: FLEX POTENTIAL ESTIMATION</h2>
            </div>
            <div className="bg-white/10 rounded-lg p-4 backdrop-blur">
              <p className="text-white font-semibold mb-3">🎯 MILP Optimization:</p>
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-purple-900/30 rounded p-3 border border-purple-400/30">
                  <p className="text-purple-200 font-semibold mb-1">G2V Potential</p>
                  <p className="text-white text-sm font-mono">calculate_G2V_potential_milp()</p>
                </div>
                <div className="bg-indigo-900/30 rounded p-3 border border-indigo-400/30">
                  <p className="text-indigo-200 font-semibold mb-1">V2G Potential</p>
                  <p className="text-white text-sm font-mono">calculate_V2G_potential_milp()</p>
                </div>
              </div>
              <div className="mt-3 pt-3 border-t border-white/20">
                <p className="text-purple-100 font-semibold">📈 Output:</p>
                <p className="text-white text-sm">G2V & V2G Potential Curves (per timestep)</p>
              </div>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex flex-col items-center">
            <div className="w-1 h-12 bg-gradient-to-b from-purple-400 to-orange-400"></div>
            <div className="w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-orange-400"></div>
          </div>

          {/* External Service Communication */}
          <div className="bg-gradient-to-r from-orange-500 to-red-500 rounded-xl p-6 shadow-2xl w-full max-w-3xl border-2 border-orange-300">
            <div className="flex items-center gap-3 mb-3">
              <Send className="w-8 h-8 text-white" />
              <h2 className="text-2xl font-bold text-white">EXTERNAL SERVICE</h2>
            </div>
            <div className="bg-white/10 rounded-lg p-4 backdrop-blur">
              <p className="text-white font-semibold mb-2">🌐 FastAPI HTTP Communication:</p>
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-green-300 font-bold">→</span>
                  <span className="text-white">Send: G2V & V2G Potentials</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-blue-300 font-bold">←</span>
                  <span className="text-white">Receive: Flex Commands per Cluster/Timestep</span>
                </div>
              </div>
              <div className="mt-3 pt-3 border-t border-white/20 bg-orange-900/30 rounded p-2">
                <p className="text-orange-100 text-sm font-semibold mb-1">Command Types:</p>
                <ul className="text-white text-sm space-y-1">
                  <li>• Absolute Setpoint: P_set(t)</li>
                  <li>• Flex Band: [P_min(t), P_max(t)]</li>
                </ul>
              </div>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex flex-col items-center">
            <div className="w-1 h-12 bg-gradient-to-b from-red-400 to-cyan-400"></div>
            <div className="w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-cyan-400"></div>
          </div>

          {/* Stage 2: Flex-Aware Scheduling */}
          <div className="bg-gradient-to-r from-cyan-600 to-blue-600 rounded-xl p-6 shadow-2xl w-full max-w-4xl border-2 border-cyan-300">
            <div className="flex items-center gap-3 mb-3">
              <Settings className="w-8 h-8 text-white" />
              <h2 className="text-2xl font-bold text-white">STAGE 2: FLEX-AWARE SMART CHARGING</h2>
            </div>
            <div className="bg-white/10 rounded-lg p-4 backdrop-blur">
              <p className="text-white font-semibold mb-3">⚡ MILP Scheduler:</p>
              <p className="text-cyan-100 font-mono text-sm mb-4">
                flex_aware_scheduling_milp(EVFleet, MultiClusterSystem, FlexCommandSet, use_target_soc)
              </p>
              
              <div className="grid grid-cols-2 gap-4 mb-4">
                <div className="bg-cyan-900/30 rounded p-3 border border-cyan-400/30">
                  <p className="text-cyan-200 font-semibold mb-2">📋 Constraints:</p>
                  <ul className="text-white text-sm space-y-1">
                    <li>• EV Arrival/Departure</li>
                    <li>• SoC Dynamics</li>
                    <li>• Min/Max SoC Limits</li>
                    <li>• Target SoC (Hard/Soft)</li>
                    <li>• Charger Capacity</li>
                    <li>• Flex Tracking</li>
                  </ul>
                </div>
                <div className="bg-blue-900/30 rounded p-3 border border-blue-400/30">
                  <p className="text-blue-200 font-semibold mb-2">📊 Outputs:</p>
                  <ul className="text-white text-sm space-y-1">
                    <li>• EV Power Schedule</li>
                    <li>• EV SoC Schedule</li>
                    <li>• Cluster Power Profile</li>
                    <li>• Flex Tracking Stats</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>

          {/* Arrow */}
          <div className="flex flex-col items-center">
            <div className="w-1 h-12 bg-gradient-to-b from-blue-400 to-green-400"></div>
            <div className="w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-green-400"></div>
          </div>

          {/* Apply & Store */}
          <div className="bg-gradient-to-r from-green-600 to-teal-600 rounded-xl p-6 shadow-2xl w-full max-w-3xl border-2 border-green-300">
            <div className="flex items-center gap-3 mb-3">
              <BarChart3 className="w-8 h-8 text-white" />
              <h2 className="text-2xl font-bold text-white">APPLY SCHEDULE & STORE RESULTS</h2>
            </div>
            <div className="bg-white/10 rounded-lg p-4 backdrop-blur">
              <div className="space-y-3">
                <div className="bg-green-900/30 rounded p-3 border border-green-400/30">
                  <p className="text-green-200 font-semibold mb-1">🔄 Apply to Objects:</p>
                  <ul className="text-white text-sm space-y-1">
                    <li>• ChargingUnit.schedule_pow</li>
                    <li>• ChargingUnit.schedule_soc</li>
                    <li>• MultiClusterSystem.databank</li>
                  </ul>
                </div>
                <div className="bg-teal-900/30 rounded p-3 border border-teal-400/30">
                  <p className="text-teal-200 font-semibold mb-1">💾 Export Results:</p>
                  <ul className="text-white text-sm space-y-1">
                    <li>• Excel/CSV Files</li>
                    <li>• Visualization Plots (KPIs, SoC, Flex)</li>
                    <li>• Service Logs</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>

          <div className="flex flex-col items-center py-4">
            <p className="text-emerald-200 mt-2 font-semibold">Service Ready: strict + best_effort scheduling with full traceability</p>
          </div>

        </div>
      </div>
    </div>
  );
}
