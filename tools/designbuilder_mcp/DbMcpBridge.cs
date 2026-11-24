/*
DbMcpBridge: lets the DesignBuilder MCP read and change zone attributes through DesignBuilder's own API.

Setup (once per model): open the model in DesignBuilder -> Scripts toolbar -> Script Manager ->
add a new C# script, paste this whole file, tick "Enable program" (and "Enable scripts"), save the model.

How it works: the MCP writes a job file and starts DesignBuilder with the environment variable DBMCP_JOB
pointing to it. When the model is loaded (or when a /process= command reaches the script) the job is run.
Results go to <job>.out and every hook call is logged to <job>.log, so the MCP can see what happened.
Without DBMCP_JOB the script does nothing, so it is harmless during normal use.

Job file lines (tab separated):
  dump<TAB>Attr1,Attr2,...            write the effective value of each attribute for every zone
  set<TAB>zone<TAB>Attr<TAB>value     zone = IDF name, zone name, or * for all zones
  save                                save the model

Keep this file ASCII only and do not use the logical-or operator:
the DB script editor drops non-ASCII text and rewrites the two pipe characters as _OR_.
*/

using System;
using System.Collections.Generic;
using System.IO;
using DB.Api;
using DB.Extensibility.Contracts;

namespace DB.Extensibility.Scripts
{
    public class DbMcpBridge : ScriptBase, IScript
    {
        static bool done = false;

        string JobPath()
        {
            return System.Environment.GetEnvironmentVariable("DBMCP_JOB");
        }

        void Log(string message)
        {
            string job = JobPath();
            if (string.IsNullOrEmpty(job))
                return;
            File.AppendAllText(job + ".log", DateTime.Now.ToString("HH:mm:ss") + " " + message + System.Environment.NewLine);
        }

        public override void ModelLoaded()
        {
            Log("hook ModelLoaded");
            RunJob("ModelLoaded");
        }

        public override void AtCommandLine(string command)
        {
            Log("hook AtCommandLine: " + command);
            RunJob("AtCommandLine");
        }

        public override void ScreenChanged(ScreenCode screen)
        {
            Log("hook ScreenChanged: " + screen.ToString());
        }

        // Under /process= DesignBuilder loads scripts only when a calculation starts, so the job also runs
        // just before the IDF is generated; changes made here are used by that calculation.
        public override void BeforeEnergyIdfGeneration()
        {
            Log("hook BeforeEnergyIdfGeneration");
            RunJob("BeforeEnergyIdfGeneration");
        }

        public override void BeforeHeatingIdfGeneration()
        {
            Log("hook BeforeHeatingIdfGeneration");
            RunJob("BeforeHeatingIdfGeneration");
        }

        public override void BeforeCoolingIdfGeneration()
        {
            Log("hook BeforeCoolingIdfGeneration");
            RunJob("BeforeCoolingIdfGeneration");
        }

        public override void BeforeEnergySimulation()
        {
            Log("hook BeforeEnergySimulation");
        }

        public override void AfterEnergySimulation()
        {
            Log("hook AfterEnergySimulation");
        }

        public override void AfterHeatingSimulation()
        {
            Log("hook AfterHeatingSimulation");
        }

        public override void AfterCoolingSimulation()
        {
            Log("hook AfterCoolingSimulation");
        }

        void RunJob(string from)
        {
            string job = JobPath();
            if (done)
                return;
            if (string.IsNullOrEmpty(job))
                return;
            if (!File.Exists(job))
            {
                Log("no job file " + job);
                return;
            }
            done = true;
            var output = new List<string>();
            try
            {
                Building building = ActiveBuilding;
                if (building == null)
                    building = ApiEnvironment.Site.Buildings[0];
                output.Add("info\trun from " + from + "\tmodel " + ApiEnvironment.DsbFilePath);
                foreach (string raw in File.ReadAllLines(job))
                {
                    string[] f = raw.Split('\t');
                    if (f.Length == 0)
                        continue;
                    string op = f[0].Trim();
                    if (op == "dump" && f.Length > 1)
                        Dump(building, f[1].Split(','), output);
                    else if (op == "set" && f.Length > 3)
                        Set(building, f[1], f[2], f[3], output);
                    else if (op == "save")
                        output.Add("save\t" + ApiEnvironment.DocumentFileOperations.Save());
                }
                output.Add("done");
            }
            catch (Exception e)
            {
                output.Add("error\t" + e.GetType().Name + "\t" + e.Message.Replace("\r", " ").Replace("\n", " "));
            }
            File.WriteAllLines(job + ".out", output);
            Log("job finished, " + output.Count + " lines");
        }

        void Dump(Building building, string[] attrs, List<string> output)
        {
            int b = 0;
            foreach (BuildingBlock block in building.BuildingBlocks)
            {
                foreach (Zone zone in block.Zones)
                {
                    var cells = new List<string>();
                    cells.Add("zone");
                    cells.Add(b.ToString());
                    cells.Add(block.GetAttribute("Title"));
                    cells.Add(zone.Name);
                    cells.Add(zone.IdfName);
                    cells.Add(zone.FloorArea.ToString("0.###"));
                    foreach (string a in attrs)
                        cells.Add(a.Trim() + "=" + zone.GetAttribute(a.Trim()));
                    output.Add(string.Join("\t", cells.ToArray()));
                }
                b++;
            }
        }

        void Set(Building building, string target, string attr, string value, List<string> output)
        {
            int n = 0;
            foreach (BuildingBlock block in building.BuildingBlocks)
            {
                foreach (Zone zone in block.Zones)
                {
                    bool match = target == "*";
                    if (!match)
                        match = string.Equals(zone.IdfName, target, StringComparison.OrdinalIgnoreCase);
                    if (!match)
                        match = string.Equals(zone.Name, target, StringComparison.OrdinalIgnoreCase);
                    if (!match)
                        continue;
                    string before = zone.GetAttribute(attr);
                    zone.SetAttribute(attr, value);
                    output.Add("set\t" + zone.IdfName + "\t" + attr + "\t" + before + " -> " + zone.GetAttribute(attr));
                    n++;
                }
            }
            if (n == 0)
                output.Add("set\t" + target + "\t" + attr + "\tno matching zone");
        }
    }
}
