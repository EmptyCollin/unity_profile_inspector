using UnityEditor;
using UnityEditor.AddressableAssets;
using UnityEditor.AddressableAssets.Settings;
using UnityEngine;

namespace DevSim.Editor
{
    /// <summary>一次性把 shop payload 标成 addressable；工程内不包含期望结果。</summary>
    [InitializeOnLoad]
    public static class DevSimAddressablesSetup
    {
        static DevSimAddressablesSetup()
        {
            EditorApplication.delayCall += Setup;
        }

        [MenuItem("DevSim/Setup Addressables")]
        public static void Setup()
        {
            var settings = AddressableAssetSettingsDefaultObject.Settings;
            if (settings == null)
                settings = AddressableAssetSettingsDefaultObject.GetSettings(true);
            if (settings == null)
            {
                Debug.LogWarning("DevSim: Addressables settings 不可用");
                return;
            }

            var group = settings.FindGroup("DevSim");
            if (group == null)
                group = settings.CreateGroup("DevSim", false, false, false, null);

            for (int i = 0; i < 8; i++)
            {
                string path = "Assets/Resources/DevSim/payload_" + i + ".txt";
                string guid = AssetDatabase.AssetPathToGUID(path);
                if (string.IsNullOrEmpty(guid)) continue;
                var entry = settings.CreateOrMoveEntry(guid, group);
                entry.address = "devsim/payload_" + i;
                entry.SetLabel("shop", true, true);
            }
            settings.SetDirty(AddressableAssetSettings.ModificationEvent.BatchModification, null, true, true);
            AssetDatabase.SaveAssets();
            Debug.Log("DevSim: Addressables group ready");
        }
    }
}
