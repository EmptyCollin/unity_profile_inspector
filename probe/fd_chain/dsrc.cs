using System;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;
var sb=new StringBuilder();
const BindingFlags SF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Static;
const BindingFlags IF=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance;
Type util=null,win=null,dsT=null,treeT=null;
foreach(var a in AppDomain.CurrentDomain.GetAssemblies()){Type[] ts;try{ts=a.GetTypes();}catch(Exception){continue;}
 util=util??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerUtility");
 win=win??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerWindow");
 treeT=treeT??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerTreeView");
 dsT=dsT??ts.FirstOrDefault(x=>x.Name=="FrameDebuggerTreeViewDataSource");}
var en=typeof(UnityEngine.FrameDebugger).GetProperty("enabled",SF);
sb.Append("enabled=").Append(en.GetValue(null,null)).Append(" isPaused=").Append(EditorApplication.isPaused);
sb.Append(" count=").Append(Convert.ToString(util.GetProperty("count",SF).GetValue(null,null)));
sb.Append(" limit=").Append(Convert.ToString(util.GetProperty("limit",SF).GetValue(null,null))).Append(Environment.NewLine);
// 用 FindObjectsOfType 取窗口实例，比 OpenWindow 稳
object w=null;
try{ var ms=Resources.FindObjectsOfTypeAll(win); sb.Append("窗口实例数=").Append(ms.Length).Append(Environment.NewLine); if(ms.Length>0)w=ms[0]; }catch(Exception e){sb.Append("查找异常").Append(Environment.NewLine);}
if(w==null){try{w=win.GetMethod("OpenWindow",SF).Invoke(null,null);sb.Append("改用 OpenWindow").Append(Environment.NewLine);}catch(Exception){}}
object tree=null;
try{ var ft=win.GetField("m_TreeView",IF); sb.Append("字段 m_TreeView 存在=").Append(ft!=null).Append(Environment.NewLine); if(ft!=null)tree=ft.GetValue(w); }
catch(Exception e){sb.Append("取树异常 ").Append((e.InnerException??e).GetType().Name).Append(": ").Append((e.InnerException??e).Message).Append(Environment.NewLine);}
sb.Append("tree=").Append(tree==null?"NULL":tree.GetType().Name).Append(Environment.NewLine);
object ds=null;
if(tree!=null){ try{ ds=treeT.GetField("m_DataSource",IF).GetValue(tree);}catch(Exception e){sb.Append("取DataSource异常 ").Append((e.InnerException??e).GetType().Name).Append(Environment.NewLine);} }
sb.Append("dataSource=").Append(ds==null?"NULL":ds.GetType().Name).Append(Environment.NewLine);
if(ds!=null)
{
  sb.Append("== DataSource 字段 ==").Append(Environment.NewLine);
  foreach(var f in ds.GetType().GetFields(IF|SF).Where(x=>!x.IsLiteral))
  {
    object v=null; try{v=f.GetValue(f.IsStatic?null:ds);}catch(Exception){continue;}
    string d;
    if(v==null)d="null";
    else if(v is Array av)d=f.FieldType.Name+" len="+av.Length;
    else if(v is int||v is bool||v is long||v is float||v is double)d=f.FieldType.Name+"="+v;
    else d=f.FieldType.Name;
    sb.Append("  ").Append(f.Name).Append(" : ").Append(d).Append(f.IsStatic?" [s]":"").Append(Environment.NewLine);
  }
  sb.Append("== DataSource 方法 ==").Append(Environment.NewLine);
  foreach(var m in ds.GetType().GetMethods(IF|SF).Where(x=>!x.IsSpecialName&&!x.Name.EndsWith("_Injected")&&x.DeclaringType==ds.GetType()))
    sb.Append("  ").Append(m.ReturnType.Name).Append(" ").Append(m.Name).Append("(")
      .Append(string.Join(",",m.GetParameters().Select(p=>p.ParameterType.Name))).Append(")").Append(m.IsStatic?" [s]":"").Append(Environment.NewLine);
}
return sb.ToString();
