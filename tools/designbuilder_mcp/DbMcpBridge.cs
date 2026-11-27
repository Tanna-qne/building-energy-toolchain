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
  bdump<TAB>Attr1,Attr2,...           the same for the building
  bset<TAB>Attr<TAB>value             set a building attribute
  table<TAB>Table<TAB>id1,id2,...     write every field of these records of a model table (Constructions, Schedules, ...);
                                      @n means the n-th record, +n every record with id n or more
  copy<TAB>Table<TAB>id<TAB>k=v...    add a record copied from record id, with the given fields changed;
                                      later lines can use its new id as $NEW1, $NEW2, ...
  importxml<TAB>path                  import a dsbXML file (Site.ImportXMLFile); later lines work on the last building
  sdump<TAB>Attr1,... / sset<TAB>Attr<TAB>value   read / set site attributes (HourlyOutput, ...)
  handles                             zone handle, block, zone name and IDF name of every zone
  groundfix<TAB>id                   zones whose ground floor has no exposed perimeter get ground floor construction id
  saveas<TAB>name<TAB>folder          save the model as folder\name.dsb
  quit                                end DesignBuilder once the job is written (after importxml)
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
        List<string> added = new List<string>();

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

        // DBMCP_PHASE=after: run the job only once the calculation has finished. Importing a model while
        // DesignBuilder is generating the IDF replaces objects it is still using and DB fails ("Automation error").
        bool AfterPhase()
        {
            return System.Environment.GetEnvironmentVariable("DBMCP_PHASE") == "after";
        }

        public override void AfterHeatingSimulation()
        {
            Log("hook AfterHeatingSimulation");
            if (AfterPhase())
                RunJob("AfterHeatingSimulation", true);
        }

        public override void AfterCoolingSimulation()
        {
            Log("hook AfterCoolingSimulation");
        }

        void RunJob(string from)
        {
            RunJob(from, false);
        }

        void RunJob(string from, bool after)
        {
            string job = JobPath();
            if (done)
                return;
            if (AfterPhase() != after)
                return;
            if (string.IsNullOrEmpty(job))
                return;
            if (!File.Exists(job))
            {
                Log("no job file " + job);
                return;
            }
            done = true;
            bool quit = false;
            var output = new List<string>();
            try
            {
                Building building = ActiveBuilding;
                if (building == null)
                    building = ApiEnvironment.Site.Buildings[0];
                output.Add("info\trun from " + from + "\tmodel " + ApiEnvironment.DsbFilePath);
                int lineNo = 0;
                foreach (string line in File.ReadAllLines(job))
                {
                    lineNo++;
                    // $NEW1, $NEW2 ... stand for the ids of the records added by earlier copy lines
                    string raw = line;
                    for (int k = added.Count; k >= 1; k--)
                        raw = raw.Replace("$NEW" + k, added[k - 1]);
                    string[] f = raw.Split('\t');
                    if (f.Length == 0)
                        continue;
                    string op = f[0].Trim();
                    try
                    {
                        if (op == "dump" && f.Length > 1)
                            Dump(building, f[1].Split(','), output);
                        else if (op == "set" && f.Length > 3)
                            Set(building, f[1], f[2], f[3], output);
                        else if (op == "bdump" && f.Length > 1)
                            BuildingDump(building, f[1].Split(','), output);
                        else if (op == "bset" && f.Length > 2)
                        {
                            string before = building.GetAttribute(f[1]);
                            building.SetAttribute(f[1], f[2]);
                            output.Add("bset\t" + f[1] + "\t" + before + "\t" + building.GetAttribute(f[1]));
                        }
                        else if (op == "table" && f.Length > 2)
                            TableDump(f[1], f[2].Split(','), output);
                        else if (op == "copy" && f.Length > 2)
                            TableCopy(f, output);
                        else if (op == "importxml" && f.Length > 1)
                            building = ImportXml(building, f[1], output);
                        else if (op == "sset" && f.Length > 2)
                        {
                            string before = ApiEnvironment.Site.GetAttribute(f[1]);
                            ApiEnvironment.Site.SetAttribute(f[1], f[2]);
                            output.Add("sset\t" + f[1] + "\t" + before + "\t" + ApiEnvironment.Site.GetAttribute(f[1]));
                        }
                        else if (op == "sdump" && f.Length > 1)
                        {
                            var cells = new List<string>();
                            cells.Add("site");
                            foreach (string a in f[1].Split(','))
                                cells.Add(a.Trim() + "=" + Clean(ApiEnvironment.Site.GetAttribute(a.Trim())));
                            output.Add(string.Join("\t", cells.ToArray()));
                        }
                        else if (op == "handles")
                        {
                            // with numeric IDF names (evaluation licence, IDFNameType=2) EnergyPlus zones are named by handle
                            foreach (BuildingBlock block in building.BuildingBlocks)
                                foreach (Zone zone in block.Zones)
                                    output.Add("handle\t" + zone.Handle + "\t" + block.GetAttribute("Title") + "\t" + zone.Name + "\t" + zone.IdfName);
                        }
                        else if (op == "groundfix" && f.Length > 1)
                            GroundFix(building, f[1], output);
                        else if (op == "saveas" && f.Length > 2)
                            output.Add("saveas\t" + ApiEnvironment.DocumentFileOperations.SaveAs(f[1], f[2]));
                        else if (op == "quit")
                            quit = true;
                        else if (op == "save")
                            output.Add("save\t" + ApiEnvironment.DocumentFileOperations.Save());
                    }
                    catch (Exception e)
                    {
                        // one bad line (for example an attribute this zone does not have) must not stop the job
                        output.Add("lineerror\t" + lineNo + "\t" + e.GetType().Name + "\t" + e.Message.Replace("\r", " ").Replace("\n", " "));
                    }
                }
                output.Add("done");
            }
            catch (Exception e)
            {
                output.Add("error\t" + e.GetType().Name + "\t" + e.Message.Replace("\r", " ").Replace("\n", " "));
            }
            File.WriteAllLines(job + ".out", output);
            Log("job finished, " + output.Count + " lines");
            if (quit)
            {
                // the calculation that loaded this script would go on with objects the job replaced and fail
                Log("quit");
                System.Diagnostics.Process.GetCurrentProcess().Kill();
            }
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

        // Importing replaces the whole site, including the model's own scripts (EMSPrograms records from 10000)
        // and the switches that select them, so keep them and put them back: the new model keeps this bridge.
        Building ImportXml(Building building, string path, List<string> output)
        {
            string[] keys = { "EMSOn", "EMSId" };
            var attrs = new Dictionary<string, string>();
            foreach (string k in keys)
                attrs[k] = building.GetAttribute(k);
            var scripts = new List<Dictionary<string, string>>();
            Table t = ApiEnvironment.Site.GetTable("EMSPrograms");
            int v;
            if (t != null)
                foreach (Record r in t.Records)
                    if (int.TryParse(r["Id"], out v) && v >= 10000)
                    {
                        var copy = new Dictionary<string, string>();
                        for (int i = 0; i < r.FieldCount; i++)
                            copy[r.GetKeyFromIndex(i)] = r[i];
                        scripts.Add(copy);
                    }
            output.Add("info\tkept " + scripts.Count + " scripts, EMSOn=" + attrs["EMSOn"] + " EMSId=" + attrs["EMSId"]);

            bool ok = ApiEnvironment.Site.ImportXMLFile(path);
            building = ApiEnvironment.Site.Buildings[ApiEnvironment.Site.Buildings.Count - 1];
            output.Add("importxml\t" + ok + "\tbuildings=" + ApiEnvironment.Site.Buildings.Count);

            Table nt = ApiEnvironment.Site.GetTable("EMSPrograms");
            foreach (var s in scripts)
            {
                Record r = nt.AddRecord();
                foreach (var kv in s)
                    r[kv.Key] = kv.Value;
                output.Add("restored\tEMSPrograms\t" + r["Id"]);
            }
            foreach (string k in keys)
                if (!string.IsNullOrEmpty(attrs[k]))
                    building.SetAttribute(k, attrs[k]);
            output.Add("info\tafter import EMSOn=" + building.GetAttribute("EMSOn") + " EMSId=" + building.GetAttribute("EMSId"));
            return building;
        }

        // An F-factor ground floor needs an exposed perimeter; DesignBuilder refuses to calculate a zone whose
        // ground floor has none (an interior core zone). Give those zones the layered construction instead.
        void GroundFix(Building building, string layeredId, List<string> output)
        {
            foreach (BuildingBlock block in building.BuildingBlocks)
            {
                foreach (Zone zone in block.Zones)
                {
                    double area = 0, length = 0;
                    foreach (Surface s in zone.Surfaces)
                        foreach (Adjacency a in s.Adjacencies)
                            if (a.AdjacencyCondition == AdjacencyCondition.AdjacentToGround)
                            {
                                area += a.Area;
                                length += a.ExposedWallGroundFloorLength;
                            }
                    if (area <= 0)
                        continue;
                    output.Add("ground\t" + zone.Name + "\t" + zone.IdfName + "\t" + area.ToString("0.##") + "\t" + length.ToString("0.##"));
                    if (length < 0.01)
                    {
                        zone.SetAttribute("CombinedGroundFloorConstr", layeredId);
                        output.Add("groundfix\t" + zone.Name + "\t" + zone.GetAttribute("CombinedGroundFloorConstr"));
                    }
                }
            }
        }

        static string Clean(string s)
        {
            if (s == null)
                return "";
            return s.Replace("\t", " ").Replace("\r", " ").Replace("\n", " ");
        }

        void BuildingDump(Building building, string[] attrs, List<string> output)
        {
            var cells = new List<string>();
            cells.Add("building");
            foreach (string a in attrs)
                cells.Add(a.Trim() + "=" + Clean(building.GetAttribute(a.Trim())));
            output.Add(string.Join("\t", cells.ToArray()));
        }

        Record FindRecord(Table table, string id)
        {
            foreach (Record r in table.Records)
                if (r["Id"] == id)
                    return r;
            return null;
        }

        string RecordLine(string tableName, Record r)
        {
            var cells = new List<string>();
            cells.Add("rec");
            cells.Add(tableName);
            cells.Add(r["Id"]);
            for (int i = 0; i < r.FieldCount; i++)
                cells.Add(r.GetKeyFromIndex(i) + "=" + Clean(r[i]));
            return string.Join("\t", cells.ToArray());
        }

        void TableDump(string tableName, string[] ids, List<string> output)
        {
            Table table = ApiEnvironment.Site.GetTable(tableName);
            if (table == null)
            {
                output.Add("notable\t" + tableName);
                return;
            }
            output.Add("table\t" + tableName + "\t" + table.Records.Count);
            foreach (string id in ids)
            {
                if (id.Trim().StartsWith("+"))
                {
                    // +n = every record whose id is n or more (the model's own records start at 10000)
                    int min = int.Parse(id.Trim().Substring(1));
                    int v;
                    foreach (Record m in table.Records)
                        if (int.TryParse(m["Id"], out v) && v >= min)
                            output.Add(RecordLine(tableName, m));
                    continue;
                }
                Record r;
                if (id.Trim().StartsWith("@"))
                    r = table.Records[int.Parse(id.Trim().Substring(1))];   // @n = the n-th record
                else
                    r = FindRecord(table, id.Trim());
                if (r == null)
                    output.Add("norec\t" + tableName + "\t" + id.Trim());
                else
                    output.Add(RecordLine(tableName, r));
            }
        }

        void TableCopy(string[] f, List<string> output)
        {
            Table table = ApiEnvironment.Site.GetTable(f[1]);
            Record src = FindRecord(table, f[2]);
            if (src == null)
            {
                output.Add("norec\t" + f[1] + "\t" + f[2]);
                return;
            }
            // Table.GetUniqueId() returned 0 in DB 2025.1, so use the largest id + 1 (the model's own range starts at 10000)
            int max = 9999;
            int v;
            foreach (Record m in table.Records)
                if (int.TryParse(m["Id"], out v) && v > max)
                    max = v;
            string id = (max + 1).ToString();
            Record dst = table.AddRecord();
            output.Add("info\tAddRecord gave id " + dst["Id"] + ", using " + id);
            for (int i = 0; i < src.FieldCount; i++)
                dst[src.GetKeyFromIndex(i)] = src[i];
            dst["Id"] = id;
            for (int i = 3; i < f.Length; i++)
            {
                int eq = f[i].IndexOf('=');
                if (eq > 0)
                    dst[f[i].Substring(0, eq)] = f[i].Substring(eq + 1);
            }
            added.Add(id);
            output.Add("added\t" + f[1] + "\t" + id);
            output.Add(RecordLine(f[1], dst));
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
                    output.Add("set\t" + zone.IdfName + "\t" + attr + "\t" + before + "\t" + zone.GetAttribute(attr));
                    n++;
                }
            }
            if (n == 0)
                output.Add("nozone\t" + target + "\t" + attr);
        }
    }
}
