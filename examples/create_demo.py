"""Create an original Chinese EPUB for checking reading and copying behavior."""
from pathlib import Path
from ebooklib import epub

def create_demo(destination: Path) -> Path:
    """Write the original demo to a caller-supplied path, without import side effects."""
    book = epub.EpubBook()
    book.set_identifier('reader3-learning-demo-v1')
    book.set_title('把问题问清楚 · 阅读演示')
    book.set_language('zh-CN')
    book.add_author('Reader3 项目演示文本')

    first = epub.EpubHtml(title='第一章：观察与解释', file_name='chapter1.xhtml', lang='zh-CN')
    first.content = '''<h1>第一章：观察与解释</h1>
    <p>这是一份为测试阅读器编写的原创演示文本，不是正式出版物。它包含中文段落、强调文字、列表和章节内的小节。</p>
    <h2 id="observe">一、先描述你看到了什么</h2>
    <p>观察，是记录可以被别人核对的事实。解释，是提出事实为什么发生的可能原因。把两者分开，有助于我们发现自己的假设。</p>
    <p>例如，“我连续三天没有读完计划中的一章”是一项观察。“我没有毅力”则是一种解释。也可能是章节太长、阅读时间不合适，或目标没有考虑实际难度。</p>
    <p>阅读时，可以先问：作者在描述<strong>事实</strong>，还是在提出<strong>解释</strong>？这句话如果不成立，会有什么可观察的证据？</p>
    <h2 id="experiment">二、用小实验检查解释</h2>
    <p>一个小实验不必解决全部问题，只需要帮助我们排除一种可能。把“每天读一章”改为“每天读十五分钟”，观察一周后的变化。</p>
    <ul><li>写下预期：更容易开始阅读。</li><li>记录结果：实际开始了几次。</li><li>回顾差异：哪些条件还没有考虑。</li></ul>
    <p>与 AI 讨论时，可以让它提出不同解释，再回到原文和自己的经历中检查。解释听起来流畅，并不代表它已经得到了证据支持。</p>'''
    second = epub.EpubHtml(title='第二章：提出一个好问题', file_name='chapter2.xhtml', lang='zh-CN')
    second.content = '''<h1>第二章：提出一个好问题</h1>
    <p>“我看不懂”是一个真实的感受，但它还没有指出困难在哪里。试着把问题缩小到一个词、一句话，或推理中的一个步骤。</p>
    <p>例如：“作者为什么从这个例子推出普遍结论？还有其他解释吗？”这样的问题让讨论有了明确的对象。</p>
    <blockquote>先说出自己的理解，再请对方指出差异，通常比直接索要总结更能暴露理解中的空缺。</blockquote>
    <p>你可以用这一章测试复制：书名和章名应当出现在正文前，段落之间应当保留空行，读完后能顺利回到上一章。</p>'''
    for item in (first, second):
        book.add_item(item)
    book.toc = [(epub.Section('第一章：观察与解释', 'chapter1.xhtml'), [
        epub.Link('chapter1.xhtml#observe', '一、先描述你看到了什么', 'observe'),
        epub.Link('chapter1.xhtml#experiment', '二、用小实验检查解释', 'experiment'),
    ]), epub.Link('chapter2.xhtml', '第二章：提出一个好问题', 'second')]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [first, second]
    epub.write_epub(str(destination), book)
    return Path(destination)


if __name__ == "__main__":
    print(create_demo(Path(__file__).resolve().parents[1] / "demo.epub"))
