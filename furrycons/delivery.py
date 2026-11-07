"""Sending decisions shared by all chat entry points; no image transformations."""


async def deliver(reply, transport, on_image_receipt):
    messages = []
    if reply.file:
        if not transport.can_send_files:
            messages.append(
                "当前接入未声明原文件附件能力，请换用支持文件的接入或联系机器人管理者。"
            )
        else:
            try:
                await transport.send_file(reply.file)
                messages.append("已向平台提交生成原文件。")
            except Exception:  # noqa: BLE001 - unrelated platform exception types
                messages.append("原文件附件发送失败，请稍后重试或联系机器人管理者。")
    if reply.image:
        try:
            receipt = await transport.send_image(reply.image)
        except Exception:  # noqa: BLE001
            if transport.can_send_files:
                try:
                    await transport.send_file(reply.image)
                    messages.append("图片已生成，但图片发送失败；已改向平台提交同一原文件附件。")
                except Exception:  # noqa: BLE001
                    messages.append(
                        "图片已生成，但图片和原文件附件均发送失败。原文件已保留，可稍后重试原图命令。"
                    )
            else:
                messages.append(
                    "图片已生成，但图片发送失败；当前接入未声明附件能力，请联系机器人管理者。"
                )
        else:
            try:
                on_image_receipt(receipt)
            except Exception:  # noqa: BLE001 - persistence failures are not send failures
                messages.append(
                    "图片已向平台提交，但引用关联暂不可用。"
                    + (
                        f"请使用查询短码 {reply.query_code}，按本条消息附带的命令选择或翻页。"
                        if reply.query_code
                        else "请使用本条消息附带的原图命令获取原文件。"
                    )
                )
    if reply.text:
        messages.append(reply.text)
    return messages
