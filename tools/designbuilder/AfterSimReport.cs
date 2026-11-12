/*
After every DB energy simulation: build the energy-saving measures report (savings, payback).

Setup (once): Scripts toolbar -> Script Manager -> new C# script, paste this whole file, enable it.
Then run Simulation -> Update data as usual. 3-10 minutes later the report opens in the browser.
Output: db_models\from_db\<model>\ (report.html, results.csv, run.log).

Keep this file ASCII only and do not use the logical-or operator:
the DB script editor drops non-ASCII text and rewrites the two pipe characters as _OR_.
*/

using System;
using System.Diagnostics;
using System.IO;
using DB.Api;
using DB.Extensibility.Contracts;

namespace DB.Extensibility.Scripts
{
    public class AfterSimReport : ScriptBase, IScript
    {
        // Change these two paths on another computer
        const string Workspace = @"C:\Users\kiri\Documents\deepseek-harness\default-workspace";
        const string Python = @"C:\Users\kiri\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe";

        public override void AfterEnergySimulation()
        {
            string epFolder = ApiEnvironment.EnergyPlusFolder;
            string idf = ApiEnvironment.EnergyPlusInputIdfPath;
            if (string.IsNullOrEmpty(idf))
                idf = Path.Combine(epFolder, "in.idf");
            if (!File.Exists(idf))
                idf = Path.Combine(epFolder, "in.idf");
            if (!File.Exists(idf))
                return;

            string name = Path.GetFileNameWithoutExtension(ApiEnvironment.DsbFilePath);
            if (string.IsNullOrEmpty(name))
                name = "db_model";
            string dest = Path.Combine(Workspace, "db_models", "from_db", name);
            Directory.CreateDirectory(dest);
            string destIdf = Path.Combine(dest, name + ".idf");
            File.Copy(idf, destIdf, true);

            // Use the same weather file as this DB run so the report matches DB's results
            string epw = Path.Combine(epFolder, "in.epw");
            string destEpw = Path.Combine(dest, "weather.epw");
            if (File.Exists(epw))
                File.Copy(epw, destEpw, true);
            else
                destEpw = "";

            var psi = new ProcessStartInfo
            {
                FileName = Python,
                Arguments = "\"" + Path.Combine(Workspace, "tools", "designbuilder", "from_db.py") + "\" \""
                            + destIdf + "\" \"" + destEpw + "\"",
                WorkingDirectory = Workspace,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            Process.Start(psi);  // do not wait: DB stays responsive
        }
    }
}
