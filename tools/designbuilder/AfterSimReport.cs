/*
DB 模擬跑完 -> 自動產生節能措施報告(省多少、幾年回本)

用法(只要設一次):
1. DB 工具列 Scripts(或 Tools -> Scripts)開啟 Script Manager,新增一個 C# script,把這整個檔案貼進去。
2. 勾選啟用這個 script。
3. 照常在 Simulation 標籤按 Update data。跑完後背景會開始算各種節能措施(約 5–10 分鐘),
   算完自動用瀏覽器打開報告。DB 不用等它,可以繼續操作。

做的事:把這次模擬用的 in.idf 與氣象檔複製到工作區 db_models/from_db/<模型名>/,
再在背景執行 tools/designbuilder/from_db.py。DB 本身的結果不受影響。

換電腦時改下面兩個路徑。
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
        const string Workspace = @"C:\Users\kiri\Documents\deepseek-harness\default-workspace";
        const string Python = @"C:\Users\kiri\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe";

        public override void AfterEnergySimulation()
        {
            string epFolder = ApiEnvironment.EnergyPlusFolder;
            string idf = ApiEnvironment.EnergyPlusInputIdfPath;
            if (string.IsNullOrEmpty(idf) || !File.Exists(idf))
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

            // 用和 DB 這次模擬同一個氣象檔, 報告才對得上 DB 的結果
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
            Process.Start(psi);  // 不等它跑完, DB 不會卡住
        }
    }
}
