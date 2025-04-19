import os
import glob
import concurrent.futures
import subprocess
import sys
import zipfile
import json
import threading
import shutil
import tempfile
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from queue import Queue

class QtProjectBuilder:
    def __init__(self, config):
        self.config = config
        self._validate_paths()
        
    def _validate_paths(self):
        required_dirs = ['bin', 'include', 'lib']
        for d in required_dirs:
            if not os.path.exists(f"{self.config['qt_path']}/{d}"):
                raise FileNotFoundError(f"无效的Qt路径: {self.config['qt_path']}")
    
    def build(self):
        start_time = datetime.now()
        print(f"[{start_time}] 开始构建过程...")
        print(f"项目路径: {self.config.get('project_path', '.')}")
        print(f"Qt路径: {self.config['qt_path']}")
        print(f"编译模式: {'静态' if self.config.get('static_build') else '动态'}链接")
        
        try:
            moc_files = self._generate_moc_files()
            objects = self._compile_sources(moc_files)
            exe_path = self._link_executable(objects)
            
            if self.config.get('pack_after_build'):
                self._package_build(exe_path)
                
            return True
        except Exception as e:
            print(f"构建失败: {str(e)}")
            return False
        finally:
            print(f"总耗时: {datetime.now() - start_time}")

    def _generate_moc_files(self):
        project_path = self.config.get('project_path', '.')
        moc_files = []
        moc_path = f"{self.config['qt_path']}/bin/moc.exe"
        # moc_output_dir = os.path.join(project_path, 'moc_temp')  # 新增moc临时目录
        moc_output_dir = os.path.join(".\\", 'moc_temp')  # 新增moc临时目录
        
        # 清空并创建moc临时目录
        if os.path.exists(moc_output_dir):
            shutil.rmtree(moc_output_dir)
        os.makedirs(moc_output_dir, exist_ok=True)

        # 修改为递归查找所有子目录的.h文件
        for root, _, files in os.walk(project_path):
            # 跳过moc临时目录
            if root.startswith(moc_output_dir):
                continue
                
            for header in files:
                if header.endswith('.h'):
                    header_path = os.path.join(root, header)
                    with open(header_path, 'r', encoding='utf-8') as f:
                        if 'Q_OBJECT' in f.read():
                            base_name = os.path.splitext(header)[0]
                            moc_file = f'moc_{base_name}.cpp'
                            # 将生成的moc文件统一存放到临时目录
                            output_path = os.path.join(moc_output_dir, moc_file)
                            subprocess.run([moc_path, header_path, '-o', output_path], check=True)
                            moc_files.append(output_path)
                            print(f'生成moc文件: {output_path}')
        return moc_files
    
    def _compile_sources(self, moc_files):
        project_path = self.config.get('project_path', '.')
        compile_cmd = [
            'g++', '-c', '-pipe',
            f'-std={self.config.get("cxx_std", "c++17")}',
            '-Wall', '-Wextra',
            f'-I{self.config["qt_path"]}/include',
            f'-I{project_path}/include',
            *[f'-I{self.config["qt_path"]}/include/Qt{module}' 
            for module in self.config.get('qt_modules', ['Core', 'Gui', 'Widgets'])]
        ]

        # 查找源文件（保持原有逻辑）
        cpp_files = []
        exclude_dirs = {'moc_temp', 'build', 'obj'}
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for file in files:
                if file.endswith('.cpp'):
                    cpp_files.append(os.path.join(root, file))

        all_sources = list(set(cpp_files + moc_files))
        print(f"[{datetime.now()}] 开始多线程编译，共 {len(all_sources)} 个源文件")

        lock = threading.Lock()
        objects = []
        failed_flag = False

        # 合并后的编译处理函数
        def compile_task(src):
            nonlocal failed_flag
            if failed_flag:
                return None

            # 创建对象文件目录
            base_name = os.path.splitext(os.path.basename(src))[0]
            obj_dir = os.path.join('obj', os.path.relpath(os.path.dirname(src), project_path))
            os.makedirs(obj_dir, exist_ok=True)
            obj = os.path.join(obj_dir, f'{base_name}.o')

            # 记录开始编译
            with lock:
                print(f"[G++][开始] 编译 {os.path.relpath(src, project_path)}")
                print(f"[G++][命令] {' '.join(compile_cmd + ['-o', obj, src])}")

            # 执行编译命令并捕获输出
            process = subprocess.Popen(
                [*compile_cmd, '-o', obj, src],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            # 实时输出处理
            output_buffer = []
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    with lock:
                        print(f"[G++][输出] {os.path.basename(src)}: {line.strip()}")
                    output_buffer.append(line.strip())

            # 处理编译结果
            if process.returncode != 0:
                with lock:
                    print(f"[G++][失败] {src}\n错误输出:\n" + '\n'.join(output_buffer[-3:]))
                    failed_flag = True
                return None

            with lock:
                objects.append(obj)
                print(f"[G++][完成] {src} -> {os.path.relpath(obj, project_path)}")
            return obj

        # 创建线程池执行任务
        max_workers = min(os.cpu_count() or 4, len(all_sources))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(compile_task, src): src for src in all_sources}
            try:
                for future in concurrent.futures.as_completed(futures):
                    if future.exception():
                        raise future.exception()
            except Exception as e:
                executor.shutdown(wait=False)
                raise

        if failed_flag:
            raise RuntimeError("编译过程中出现错误，已终止构建")

        print(f"[{datetime.now()}] 编译完成，生成 {len(objects)} 个对象文件")
        return objects
    
    def _link_executable(self, objects):
        # 处理输出路径
        output_dir = self.config.get('output_dir', './dist')
        os.makedirs(output_dir, exist_ok=True)
        exe_name = os.path.join(output_dir, 
                      f"{self.config.get('output_name', 'myapp')}.exe")
        
        link_cmd = [
            'g++',
            f'-Wl,-subsystem,{self.config.get("subsystem", "windows")}',
            f'-L{self.config["qt_path"]}/lib',
            *[f'-L{path}' for path in self.config.get('extra_lib_paths', [])],
            *self._get_link_options(),
            '-o', exe_name,
            *objects,
            '-lz', '-lopengl32', '-lGLU32', '-lgdi32', '-luser32',
            *[f'-lQt{self.config.get("qt_version", 6)}{module}' 
              for module in self.config.get('qt_modules', ['Core', 'Gui', 'Widgets'])],
            '-lmingw32', '-ldwmapi'
        ]
        print(f"[LD][命令] {' '.join(link_cmd)}")

        process = subprocess.Popen(
            link_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        
        # 实时捕获链接输出
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                print(f"[LD] {output.strip()}")
                
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, ' '.join(link_cmd))
        
        print(f'成功生成可执行文件: {exe_name}')
        return exe_name

    def _get_link_options(self):
        opts = []
        if self.config.get('static_build'):
            opts.extend([
                '-static',
                '-static-libgcc',
                '-static-libstdc++',
                '-Wl,-Bstatic',  # 强制使用静态链接
                '-lwinpthread',   # 添加Windows线程库
                '-Wl,-Bdynamic'   # 恢复动态链接系统库
            ])
        return opts

    def _package_build(self, exe_path):
        output_dir = os.path.dirname(exe_path)
        
        # 使用Popen执行windeployqt并实时输出日志
        deploy_cmd = [
            os.path.join(self.config['qt_path'], 'bin', 'windeployqt.exe'),
            '--dir', output_dir,
            '--no-translations',
            exe_path
        ]
        
        try:
            process = subprocess.Popen(
                deploy_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            
            # 实时读取输出
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    print(f"[windeployqt] {output.strip()}")  # 已存在该日志前缀
                    
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, deploy_cmd)
                
        except Exception as e:
            print(f"依赖收集失败: {str(e)}")
            raise

        # 创建临时目录打包
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                print(f"\n创建临时打包目录: {tmp_dir}")
                
                # 在临时目录创建压缩包
                build_dir = os.path.join(self.config['project_path'], 'build')
                os.makedirs(build_dir, exist_ok=True)
                final_zip = os.path.join(
                    build_dir,
                    f"{os.path.basename(exe_path[:-4])}_full.zip"
                )
                tmp_zip = os.path.join(tmp_dir, 'build.zip')
                # 计算总文件数用于进度显示
                total_files = sum([len(files) for _, _, files in os.walk(output_dir)])
                processed = 0
                
                with zipfile.ZipFile(tmp_zip, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                    for root, _, files in os.walk(output_dir):
                        for file in files:
                            src_path = os.path.join(root, file)
                            if not os.path.isfile(src_path):  # 跳过符号链接等
                                continue
                                
                            # 计算相对路径
                            arcname = os.path.relpath(src_path, output_dir)
                            print(f"正在压缩 ({processed+1}/{total_files}): {arcname}")
                            z.write(src_path, arcname)
                            processed += 1

                # 移动压缩包到最终位置
                if os.path.exists(final_zip):
                    os.remove(final_zip)
                shutil.move(tmp_zip, final_zip)

                print(f"\n成功生成分发包: {final_zip}")
                print(f"压缩包大小: {os.path.getsize(final_zip)/1024/1024:.2f} MB")

        except Exception as e:
            print(f"打包失败: {str(e)}")
            raise
# 新增辅助方法
    def _get_dir_size(self, path):
        total = 0
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += self._get_dir_size(entry.path)
        return total

    def _clean_intermediates(self, moc_files):
        for f in glob.glob('*.o') + moc_files:
            os.remove(f)
            print(f'已清理临时文件: {f}')
            
class TextRedirector:
    def __init__(self, widget):
        self.widget = widget

    def write(self, text):
        self.widget.configure(state='normal')
        self.widget.insert(tk.END, text)
        self.widget.see(tk.END)
        self.widget.configure(state='disabled')

    def flush(self):
        pass

class BuildThread(threading.Thread):
    def __init__(self, config, queue):
        super().__init__()
        self.config = config
        self.queue = queue
        
    def run(self):
        try:
            builder = QtProjectBuilder(self.config)
            if builder.build():
                self.queue.put(('done', "项目构建成功！"))
            else:
                self.queue.put(('done', "构建完成但存在警告"))
        except Exception as e:
            self.queue.put(('error', f"构建失败: {str(e)}"))

class QtBuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Qt项目构建工具 v1.0")
        self.geometry("900x600")
        self._setup_ui()
        self.event_queue = Queue()
        self.load_config()
        sys.stdout = TextRedirector(self.log_area)
        self.after(100, self.check_queue)

    def _setup_ui(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 配置面板
        config_frame = ttk.LabelFrame(main_frame, text="构建配置")
        self._build_config_panel(config_frame)
        config_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="编译日志")
        self.log_area = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, 
                                                state='disabled', font=('Consolas', 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)
        log_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    def _build_config_panel(self, parent):
        # 项目路径
        ttk.Label(parent, text="项目路径:").grid(row=0, column=0, sticky='w', pady=5)
        self.project_path = ttk.Entry(parent, width=30)
        self.project_path.grid(row=0, column=1, padx=5)
        ttk.Button(parent, text="浏览", width=8, 
                 command=lambda: self._select_path(self.project_path)).grid(row=0, column=2)

        # Qt路径
        ttk.Label(parent, text="Qt安装路径:").grid(row=1, column=0, sticky='w', pady=5)
        self.qt_path = ttk.Entry(parent, width=30)
        self.qt_path.grid(row=1, column=1)
        ttk.Button(parent, text="浏览", width=8,
                 command=lambda: self._select_path(self.qt_path)).grid(row=1, column=2)

        # 输出配置
        ttk.Label(parent, text="输出目录:").grid(row=2, column=0, sticky='w', pady=5)
        self.output_dir = ttk.Entry(parent, width=30)
        self.output_dir.grid(row=2, column=1)
        ttk.Button(parent, text="浏览", width=8,
                 command=lambda: self._select_path(self.output_dir)).grid(row=2, column=2)

        ttk.Label(parent, text="程序名称:").grid(row=3, column=0, sticky='w', pady=5)
        self.output_name = ttk.Entry(parent, width=30)
        self.output_name.grid(row=3, column=1, columnspan=2, sticky='ew')

        # 编译选项
        ttk.Label(parent, text="C++标准:").grid(row=4, column=0, sticky='w', pady=5)
        self.cxx_std = ttk.Combobox(parent, values=['c++11', 'c++14', 'c++17', 'c++20'], width=8)
        self.cxx_std.grid(row=4, column=1, sticky='w')

        # 构建选项
        self.static_build = tk.BooleanVar()
        ttk.Checkbutton(parent, text="静态编译", variable=self.static_build).grid(row=5, column=0, sticky='w')
        self.pack_after_build = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="自动打包", variable=self.pack_after_build).grid(row=5, column=1)

        # 控制按钮
        btn_frame = ttk.Frame(parent)
        self.btn_build = ttk.Button(btn_frame, text="开始构建", command=self.start_build, state='normal')
        self.btn_build.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清理项目", command=self.clean_project).pack(side=tk.LEFT)
        btn_frame.grid(row=6, column=0, columnspan=3, pady=15)
        ttk.Button(btn_frame, text="运行程序", command=self.run_program).pack(side=tk.LEFT, padx=5)
        btn_frame.grid(row=6, column=0, columnspan=3, pady=15)
        ttk.Button(btn_frame, text="清理输出", command=self.clean_output).pack(side=tk.LEFT)  # 新增按钮
        btn_frame.grid(row=6, column=0, columnspan=3, pady=15)


    def clean_output(self):
        try:
            output_dir = self.output_dir.get()
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir, ignore_errors=True)
                print(f"已清理输出目录: {output_dir}")
                messagebox.showinfo("完成", f"输出目录已清理: {output_dir}")
            else:
                messagebox.showinfo("提示", "输出目录不存在，无需清理")
        except Exception as e:
            messagebox.showerror("错误", f"清理输出目录失败: {str(e)}")

    def _select_path(self, entry_widget):
        path = filedialog.askdirectory(title="选择目录")
        if path:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, os.path.normpath(path))

    def load_config(self):
        try:
            with open('qt_builder.json', 'r') as f:
                config = json.load(f)
                self.project_path.insert(0, config.get('project_path', ''))
                self.qt_path.insert(0, config.get('qt_path', ''))
                self.output_dir.insert(0, config.get('output_dir', './dist'))
                self.output_name.insert(0, config.get('output_name', 'myapp'))
                self.cxx_std.set(config.get('cxx_std', 'c++17'))
                self.static_build.set(config.get('static_build', False))
                self.pack_after_build.set(config.get('pack_after_build', True))
        except FileNotFoundError:
            pass

    def save_config(self):
        with open('qt_builder.json', 'w') as f:
            json.dump({
                'project_path': self.project_path.get(),
                'qt_path': self.qt_path.get(),
                'output_dir': self.output_dir.get(),
                'output_name': self.output_name.get(),
                'cxx_std': self.cxx_std.get(),
                'static_build': self.static_build.get(),
                'pack_after_build': self.pack_after_build.get()
            }, f, indent=2)

    def start_build(self):
        self.btn_build['state'] = 'disabled'
        self.save_config()

        config = {
            'project_path': self.project_path.get() or '.',
            'qt_path': self.qt_path.get(),
            'output_dir': self.output_dir.get(),
            'output_name': self.output_name.get(),
            'cxx_std': self.cxx_std.get(),
            'static_build': self.static_build.get(),
            'pack_after_build': self.pack_after_build.get(),
            'subsystem': 'windows'
        }

        BuildThread(config, self.event_queue).start()

    def check_queue(self):
        while not self.event_queue.empty():
            msg_type, content = self.event_queue.get()
            if msg_type == 'done':
                messagebox.showinfo("完成", content)
                self.btn_build['state'] = 'normal'
            elif msg_type == 'error':
                messagebox.showerror("错误", content)
                self.btn_build['state'] = 'normal'
            print(content)
        self.after(100, self.check_queue)

    def clean_project(self):
        try:
            count = 0
            # 清理单个文件模式
            patterns = ['*.o', 'moc_*.cpp', '*.zip']
            for pattern in patterns:
                for f in glob.glob(pattern):
                    os.remove(f)
                    count += 1
                    print(f"删除文件: {f}")
            

            # 清理关键目录
            dirs_to_remove = ['moc_temp', 'obj', 'build']
            for dir_name in dirs_to_remove:
                if os.path.exists(dir_name):
                    shutil.rmtree(dir_name, ignore_errors=True)
                    count += 1
                    print(f"删除目录: {dir_name}")

            # 递归清理所有.o文件（包括子目录）
            for root, _, files in os.walk('.'):
                for file in files:
                    if file.endswith('.o'):
                        full_path = os.path.join(root, file)
                        os.remove(full_path)
                        count += 1
                        print(f"删除对象文件: {full_path}")

            print(f"清理完成，共清理 {count} 个项")
        except Exception as e:
            messagebox.showerror("清理错误", f"清理过程中发生错误: {str(e)}")
    
    def run_program(self):
        exe_path = os.path.normpath(
            os.path.join(self.output_dir.get(), 
                        f"{self.output_name.get() or 'myapp'}.exe")
        )
        
        if not os.path.exists(exe_path):
            messagebox.showerror("错误", f"可执行文件不存在：\n{exe_path}")
            return

        try:
            subprocess.Popen([exe_path], shell=True)
            print(f"已启动程序：{exe_path}")
        except Exception as e:
            messagebox.showerror("运行错误", f"无法启动程序：\n{str(e)}")


if __name__ == '__main__':
    app = QtBuilderApp()
    app.mainloop()
